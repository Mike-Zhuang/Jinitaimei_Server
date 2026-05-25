import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from app.clients.star import StarActivitySummary, fetch_public_activities
from app.clients.tongji import TongjiLoginError, fetch_teaching_notices, latest_notice, login_tongji
from app.clients.yikatong import (
    YikatongLoginError,
    fetch_campus_card_balance,
    login_yikatong,
)
from app.database import init_database
from app.mailer import send_email
from app.repository import (
    SubscriptionRecord,
    list_active_subscriptions,
    mark_login_failure,
    mark_login_success,
    record_notification_event,
    update_campus_card_baseline,
    update_star_activity_baseline,
    update_star_registration_open_baseline,
    update_teaching_notice_baseline,
)
from app.scheduler import (
    TASK_CAMPUS_CARD,
    TASK_STAR_PRIVATE,
    TASK_STAR_PUBLIC,
    TASK_TEACHING_NOTICE,
    finish_task,
    should_run_task,
    sleep_between_subscriptions,
)
from app.security import decrypt_secret, mask_email

logger = logging.getLogger("jinitaimei.poll")


@dataclass(frozen=True)
class TaskResult:
    success: bool
    reason: str | None = None


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
    await init_database()
    for task_name in (TASK_TEACHING_NOTICE, TASK_STAR_PUBLIC, TASK_STAR_PRIVATE, TASK_CAMPUS_CARD):
        decision = await should_run_task(task_name)
        if not decision.should_run:
            logger.info("%s skipped: %s", task_name, decision.reason)
            continue
        result = await run_task(task_name)
        next_allowed = await finish_task(
            task_name,
            success=result.success,
            failure_reason=result.reason,
        )
        logger.info("%s finished success=%s next=%s", task_name, result.success, next_allowed)


async def run_task(task_name: str) -> TaskResult:
    subscriptions = await list_active_subscriptions()
    if not subscriptions:
        return TaskResult(success=True)

    try:
        if task_name == TASK_STAR_PUBLIC:
            return await run_star_public_task(subscriptions)
        if task_name == TASK_TEACHING_NOTICE:
            return await run_teaching_notice_task(subscriptions)
        if task_name == TASK_STAR_PRIVATE:
            # v1 的个人星值仍由 App 本地同步；后端只保留低频调度状态，避免多余登录。
            logger.info("%s no-op: private STAR score mail is not enabled in v1", task_name)
            return TaskResult(success=True)
        if task_name == TASK_CAMPUS_CARD:
            return await run_campus_card_task(subscriptions)
        return TaskResult(success=True)
    except Exception as exc:  # noqa: BLE001 - job 入口需要把全局异常收敛为退避信号
        logger.warning("%s failed globally: %s", task_name, exc)
        return TaskResult(success=False, reason=str(exc)[:256])


async def run_teaching_notice_task(subscriptions: list[SubscriptionRecord]) -> TaskResult:
    targets = [item for item in subscriptions if item.teaching_notice_enabled]
    if not targets:
        return TaskResult(success=True)

    failures = 0
    for index, subscription in enumerate(targets):
        if index > 0:
            await sleep_between_subscriptions()
        try:
            await process_teaching_notice_subscription(subscription)
        except Exception as exc:  # noqa: BLE001 - 单个订阅失败不能拖垮整轮
            failures += 1
            logger.warning(
                "%s subscription=%s failed: %s",
                TASK_TEACHING_NOTICE,
                mask_email(subscription.email),
                exc,
            )
    if failures == len(targets):
        return TaskResult(success=False, reason="所有教务通知订阅处理失败")
    return TaskResult(success=True)


async def process_teaching_notice_subscription(subscription: SubscriptionRecord) -> None:
    try:
        username, password = credentials_for(subscription)
        session = await login_tongji(username, password)
        await mark_login_success(subscription.id)
    except (RuntimeError, TongjiLoginError) as exc:
        await mark_login_failure(subscription.id, str(exc))
        await notify_credential_problem(subscription, str(exc))
        raise

    notices = await fetch_teaching_notices(session)
    latest = latest_notice(notices)
    if latest is None:
        logger.info(
            "%s subscription=%s no notices",
            TASK_TEACHING_NOTICE,
            mask_email(subscription.email),
        )
        return

    latest_time_key = (
        latest.publish_time.isoformat() if latest.publish_time else latest.publish_time_text
    )
    if subscription.last_seen_teaching_notice_id is None:
        await update_teaching_notice_baseline(subscription.id, latest.id, latest_time_key)
        logger.info(
            "%s subscription=%s baseline notice=%s",
            TASK_TEACHING_NOTICE,
            mask_email(subscription.email),
            latest.id,
        )
        return

    if not is_newer_teaching_notice(subscription, latest):
        return

    inserted = await record_notification_event(
        subscription.id,
        "teaching_notice",
        str(latest.id),
        latest.title,
    )
    await update_teaching_notice_baseline(subscription.id, latest.id, latest_time_key)
    if not inserted:
        return

    await send_email(
        to_address=subscription.email,
        subject=f"教学管理信息系统新通知：{latest.title}",
        text=(
            "济你太美检测到新的教学管理信息系统通知公告。\n\n"
            f"标题：{latest.title}\n"
            f"发布时间：{latest.publish_time_text or '未知'}\n\n"
            "请打开 App 查看通知列表与详情。"
        ),
    )
    logger.info(
        "%s subscription=%s sent notice=%s",
        TASK_TEACHING_NOTICE,
        mask_email(subscription.email),
        latest.id,
    )


async def run_star_public_task(subscriptions: list[SubscriptionRecord]) -> TaskResult:
    targets = [
        item
        for item in subscriptions
        if item.star_new_activity_enabled or item.star_registration_enabled
    ]
    if not targets:
        return TaskResult(success=True)

    activities = await fetch_public_activities(pages=5)
    if not activities:
        return TaskResult(success=True)

    failures = 0
    for index, subscription in enumerate(targets):
        if index > 0:
            await sleep_between_subscriptions()
        try:
            await process_star_public_subscription(subscription, activities)
        except Exception as exc:  # noqa: BLE001
            failures += 1
            logger.warning(
                "%s subscription=%s failed: %s",
                TASK_STAR_PUBLIC,
                mask_email(subscription.email),
                exc,
            )
    if failures == len(targets):
        return TaskResult(success=False, reason="所有卓越星订阅处理失败")
    return TaskResult(success=True)


async def run_campus_card_task(subscriptions: list[SubscriptionRecord]) -> TaskResult:
    targets = [item for item in subscriptions if item.campus_card_low_balance_enabled]
    if not targets:
        return TaskResult(success=True)

    failures = 0
    for index, subscription in enumerate(targets):
        if index > 0:
            await sleep_between_subscriptions()
        try:
            await process_campus_card_subscription(subscription)
        except Exception as exc:  # noqa: BLE001 - 单个订阅失败不能拖垮整轮
            failures += 1
            logger.warning(
                "%s subscription=%s failed: %s",
                TASK_CAMPUS_CARD,
                mask_email(subscription.email),
                exc,
            )
    if failures == len(targets):
        return TaskResult(success=False, reason="所有校园卡订阅处理失败")
    return TaskResult(success=True)


async def process_star_public_subscription(
    subscription: SubscriptionRecord,
    activities: list[StarActivitySummary],
) -> None:
    selected_activities = filter_star_activities(subscription, activities)
    tracked_activities = tracked_star_activities(subscription, activities)
    current_ids = [str(activity.id) for activity in tracked_activities]
    current_open_ids = [
        str(activity.id)
        for activity in tracked_activities
        if activity.is_registration_open
    ]
    if not current_ids:
        return

    if not subscription.last_seen_star_activity_ids:
        await update_star_activity_baseline(subscription.id, current_ids)
        await update_star_registration_open_baseline(subscription.id, current_open_ids)
        logger.info(
            "%s subscription=%s baseline activities=%s",
            TASK_STAR_PUBLIC,
            mask_email(subscription.email),
            len(current_ids),
        )
        return

    seen = set(subscription.last_seen_star_activity_ids)
    previous_open = set(subscription.last_seen_star_registration_open_ids)
    new_activities = [activity for activity in selected_activities if str(activity.id) not in seen]
    followed_ids = {int(value) for value in subscription.followed_star_activity_ids}

    for activity in new_activities:
        if subscription.star_new_activity_enabled and activity.is_not_started:
            await send_star_activity_mail(subscription, activity, event_type="star_new_activity")

    # 老订阅升级到新字段时，先只补当前“报名中”基线，不补发历史报名提醒。
    if not subscription.last_seen_star_registration_open_ids:
        await update_star_activity_baseline(
            subscription.id,
            current_ids + subscription.last_seen_star_activity_ids,
        )
        await update_star_registration_open_baseline(subscription.id, current_open_ids)
        logger.info(
            "%s subscription=%s seeded registration baseline=%s",
            TASK_STAR_PUBLIC,
            mask_email(subscription.email),
            len(current_open_ids),
        )
        return

    if subscription.star_registration_enabled:
        for activity in tracked_activities:
            activity_id = str(activity.id)
            if (
                activity.is_registration_open
                and activity.id in followed_ids
                and activity_id in seen
                and activity_id not in previous_open
            ):
                await send_star_activity_mail(
                    subscription,
                    activity,
                    event_type="star_registration_open",
                )

    await update_star_activity_baseline(
        subscription.id,
        current_ids + subscription.last_seen_star_activity_ids,
    )
    await update_star_registration_open_baseline(
        subscription.id,
        current_open_ids,
    )


async def send_star_activity_mail(
    subscription: SubscriptionRecord,
    activity: StarActivitySummary,
    event_type: str,
) -> None:
    inserted = await record_notification_event(
        subscription.id,
        event_type,
        str(activity.id),
        activity.title,
    )
    if not inserted:
        return

    if event_type == "star_registration_open":
        subject = f"卓越星活动报名中：{activity.title}"
        heading = "你关注的卓越星分类有活动正在报名。"
    else:
        subject = f"卓越星新活动：{activity.title}"
        heading = "济你太美检测到新的卓越星活动。"

    await send_email(
        to_address=subscription.email,
        subject=subject,
        text=(
            f"{heading}\n\n"
            f"标题：{activity.title}\n"
            f"分类：{activity.module_name or '未知'}\n"
            f"状态：{activity.progress_name or '未知'}\n"
            f"星值：{activity.points}\n"
            f"时间：{format_datetime(activity.activity_start_time)}\n"
            f"地点：{activity.location or '未知'}\n"
            f"链接：{activity.link}\n"
        ),
    )
    logger.info(
        "%s subscription=%s sent %s=%s",
        TASK_STAR_PUBLIC,
        mask_email(subscription.email),
        event_type,
        activity.id,
    )


async def process_campus_card_subscription(subscription: SubscriptionRecord) -> None:
    try:
        username, password = credentials_for(subscription)
        session = await login_yikatong(username, password)
        await mark_login_success(subscription.id)
    except (RuntimeError, TongjiLoginError, YikatongLoginError) as exc:
        await mark_login_failure(subscription.id, str(exc))
        await notify_credential_problem(subscription, str(exc))
        raise

    snapshot = await fetch_campus_card_balance(session)
    threshold = max(0.0, float(subscription.campus_card_low_balance_threshold))
    is_low = snapshot.balance_yuan <= threshold
    previous_state = subscription.last_seen_campus_card_is_low

    # 首次只建基线，不补发历史低余额邮件。
    if previous_state is None:
        await update_campus_card_baseline(subscription.id, snapshot.balance_yuan, is_low)
        logger.info(
            "%s subscription=%s baseline balance=%.2f low=%s",
            TASK_CAMPUS_CARD,
            mask_email(subscription.email),
            snapshot.balance_yuan,
            is_low,
        )
        return

    if not previous_state and is_low:
        inserted = await record_notification_event(
            subscription.id,
            "campus_card_low_balance",
            f"{snapshot.captured_at.isoformat()}:{snapshot.balance_yuan:.2f}",
            f"校园卡余额 {snapshot.balance_yuan:.2f} 元",
        )
        if inserted:
            await send_email(
                to_address=subscription.email,
                subject=f"校园卡余额偏低：¥{snapshot.balance_yuan:.2f}",
                text=(
                    "济你太美检测到你的校园卡余额已低于提醒阈值。\n\n"
                    f"当前余额：¥{snapshot.balance_yuan:.2f}\n"
                    f"提醒阈值：¥{threshold:.2f}\n"
                    f"账户：{snapshot.account or '未知'}\n"
                    f"更新时间：{format_datetime(snapshot.captured_at)}\n\n"
                    "只有在余额从高于阈值再次跌破阈值时，才会再次提醒。"
                ),
            )
            logger.info(
                "%s subscription=%s sent low balance %.2f",
                TASK_CAMPUS_CARD,
                mask_email(subscription.email),
                snapshot.balance_yuan,
            )

    await update_campus_card_baseline(subscription.id, snapshot.balance_yuan, is_low)


def credentials_for(subscription: SubscriptionRecord) -> tuple[str, str]:
    if not subscription.tongji_username or not subscription.encrypted_tongji_password:
        raise RuntimeError("缺少后端邮件推送凭据")
    return subscription.tongji_username, decrypt_secret(subscription.encrypted_tongji_password)


async def notify_credential_problem(subscription: SubscriptionRecord, reason: str) -> None:
    inserted = await record_notification_event(
        subscription.id,
        "credential_problem",
        datetime.now(UTC).strftime("%Y%m%d"),
        "邮件推送凭据需要重新确认",
    )
    if not inserted:
        return
    await send_email(
        to_address=subscription.email,
        subject="济你太美邮件提醒需要重新确认凭据",
        text=(
            "服务器尝试为你同步需要登录的通知时失败。\n\n"
            f"原因：{reason}\n\n"
            "请打开 App，在 设置 → 通知 → 邮件推送 中重新保存同济账号密码。"
            "如果学校要求验证码或二次验证，服务器不会绕过验证。"
        ),
    )


def is_newer_teaching_notice(subscription: SubscriptionRecord, latest) -> bool:
    if latest.publish_time and subscription.last_seen_teaching_notice_time:
        try:
            old_time = datetime.fromisoformat(subscription.last_seen_teaching_notice_time)
        except ValueError:
            old_time = None
        if old_time and latest.publish_time > old_time:
            return True
        if old_time and latest.publish_time == old_time:
            return latest.id != subscription.last_seen_teaching_notice_id
        return old_time is None
    return latest.id != subscription.last_seen_teaching_notice_id


def filter_star_activities(
    subscription: SubscriptionRecord,
    activities: list[StarActivitySummary],
) -> list[StarActivitySummary]:
    selected = {code for code in subscription.selected_star_module_codes if code}
    if not selected:
        return activities
    return [activity for activity in activities if activity.module_code in selected]


def tracked_star_activities(
    subscription: SubscriptionRecord,
    activities: list[StarActivitySummary],
) -> list[StarActivitySummary]:
    followed_ids = {int(value) for value in subscription.followed_star_activity_ids}
    if not followed_ids:
        return filter_star_activities(subscription, activities)

    selected = {activity.id for activity in filter_star_activities(subscription, activities)}
    tracked_ids = selected.union(followed_ids)
    return [activity for activity in activities if activity.id in tracked_ids]


def format_datetime(value: datetime | None) -> str:
    if value is None:
        return "未知"
    return value.astimezone().strftime("%Y-%m-%d %H:%M")


if __name__ == "__main__":
    asyncio.run(main())
