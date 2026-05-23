import asyncio
import logging
from dataclasses import dataclass

from app.database import init_database
from app.mailer import send_email
from app.repository import (
    SubscriptionRecord,
    list_active_subscriptions,
    mark_login_failure,
)
from app.scheduler import (
    TASK_STAR_PRIVATE,
    TASK_STAR_PUBLIC,
    TASK_TEACHING_NOTICE,
    finish_task,
    should_run_task,
    sleep_between_subscriptions,
)
from app.security import mask_email

logger = logging.getLogger("jinitaimei.poll")


@dataclass(frozen=True)
class TaskResult:
    success: bool
    reason: str | None = None


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
    await init_database()
    for task_name in (TASK_TEACHING_NOTICE, TASK_STAR_PUBLIC, TASK_STAR_PRIVATE):
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

    failures = 0
    for index, subscription in enumerate(subscriptions):
        if index > 0:
            await sleep_between_subscriptions()
        try:
            await process_subscription(task_name, subscription)
        except Exception as exc:  # noqa: BLE001 - 轮询任务必须隔离单个订阅失败
            failures += 1
            logger.warning(
                "%s subscription=%s failed: %s",
                task_name,
                mask_email(subscription.email),
                exc,
            )
    if failures == len(subscriptions):
        return TaskResult(success=False, reason="所有订阅处理失败")
    return TaskResult(success=True)


async def process_subscription(task_name: str, subscription: SubscriptionRecord) -> None:
    if task_name == TASK_TEACHING_NOTICE and not subscription.teaching_notice_enabled:
        return
    if task_name == TASK_STAR_PUBLIC and not subscription.star_new_activity_enabled:
        return
    if task_name == TASK_STAR_PRIVATE and not subscription.star_registration_enabled:
        return

    if task_name in {TASK_TEACHING_NOTICE, TASK_STAR_PRIVATE}:
        await ensure_credentials(subscription, task_name)

    # v1 先完成低频调度、加密凭据和去重基础设施。具体学校接口轮询会在
    # 后续同一个 job 中接入；这里保持成功，避免空实现反复触发失败退避和邮件轰炸。
    logger.info("%s subscription=%s ready", task_name, mask_email(subscription.email))


async def ensure_credentials(subscription: SubscriptionRecord, task_name: str) -> None:
    if subscription.tongji_username and subscription.encrypted_tongji_password:
        return
    reason = "缺少后端邮件推送凭据"
    await mark_login_failure(subscription.id, reason)
    if task_name == TASK_TEACHING_NOTICE:
        await send_email(
            to_address=subscription.email,
            subject="济你太美邮件提醒需要重新确认凭据",
            text=(
                "你已开启离线邮件提醒，但服务器没有可用的同济校园账号凭据。"
                "请在 App 的 设置 → 通知 → 邮件推送 中重新保存账号密码。"
            ),
        )
    raise RuntimeError(reason)


if __name__ == "__main__":
    asyncio.run(main())
