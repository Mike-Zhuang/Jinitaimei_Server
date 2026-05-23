import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

from app.config import Settings, get_settings
from app.repository import get_polling_state, upsert_polling_state


@dataclass(frozen=True)
class PollDecision:
    should_run: bool
    reason: str
    next_allowed_at: datetime | None


@dataclass(frozen=True)
class PollWindow:
    min_minutes: int
    max_minutes: int
    night_disabled: bool = False


TASK_TEACHING_NOTICE = "teaching_notice"
TASK_STAR_PUBLIC = "star_public"
TASK_STAR_PRIVATE = "star_private"


async def should_run_task(task_name: str, now: datetime | None = None) -> PollDecision:
    current = now or datetime.now(UTC)
    state = await get_polling_state(task_name)
    if state and state.next_allowed_at and current < state.next_allowed_at:
        return PollDecision(
            should_run=False,
            reason=f"未到下次允许运行时间 {state.next_allowed_at.isoformat()}",
            next_allowed_at=state.next_allowed_at,
        )
    return PollDecision(should_run=True, reason="到达允许运行时间", next_allowed_at=None)


async def finish_task(
    task_name: str,
    success: bool,
    failure_reason: str | None = None,
    now: datetime | None = None,
) -> datetime:
    current = now or datetime.now(UTC)
    settings = get_settings()
    state = await get_polling_state(task_name)
    failure_count = 0 if success else (state.failure_count + 1 if state else 1)
    next_allowed_at = next_poll_time(task_name, failure_count, current, settings)
    await upsert_polling_state(
        task_name=task_name,
        next_allowed_at=next_allowed_at,
        failure_count=failure_count,
        failure_reason=None if success else failure_reason,
    )
    return next_allowed_at


def next_poll_time(
    task_name: str,
    failure_count: int,
    now: datetime,
    settings: Settings | None = None,
) -> datetime:
    config = settings or get_settings()
    window = _window_for_task(task_name, _is_night(now, config), config)
    if window.night_disabled:
        return _next_daytime_start(now, config) + _random_delta(30, 90)

    min_minutes = max(1, window.min_minutes)
    max_minutes = max(min_minutes, window.max_minutes)
    if failure_count > 0:
        backoff_min = min(
            config.poll_max_backoff_minutes,
            min_minutes * (2 ** min(failure_count, 5)),
        )
        backoff_max = min(config.poll_max_backoff_minutes, max(backoff_min, backoff_min * 2))
        min_minutes, max_minutes = backoff_min, backoff_max
    return now + _random_delta(min_minutes, max_minutes)


async def sleep_between_subscriptions() -> None:
    # 这里的短暂停顿只用于错开批处理，避免同一秒集中请求学校系统。
    import asyncio

    await asyncio.sleep(secrets.randbelow(8) + 3)


def _window_for_task(task_name: str, is_night: bool, settings: Settings) -> PollWindow:
    if task_name == TASK_TEACHING_NOTICE:
        return PollWindow(
            settings.poll_teaching_notice_night_min_minutes
            if is_night
            else settings.poll_teaching_notice_day_min_minutes,
            settings.poll_teaching_notice_night_max_minutes
            if is_night
            else settings.poll_teaching_notice_day_max_minutes,
        )
    if task_name == TASK_STAR_PUBLIC:
        return PollWindow(
            settings.poll_star_public_night_min_minutes
            if is_night
            else settings.poll_star_public_day_min_minutes,
            settings.poll_star_public_night_max_minutes
            if is_night
            else settings.poll_star_public_day_max_minutes,
        )
    if task_name == TASK_STAR_PRIVATE:
        if is_night:
            return PollWindow(0, 0, night_disabled=True)
        return PollWindow(
            settings.poll_star_private_day_min_minutes,
            settings.poll_star_private_day_max_minutes,
        )
    raise ValueError(f"未知轮询任务: {task_name}")


def _is_night(now: datetime, settings: Settings) -> bool:
    local_time = now.astimezone().time()
    day_start = _parse_time(settings.poll_day_start)
    night_start = _parse_time(settings.poll_night_start)
    if night_start >= day_start:
        return local_time >= night_start or local_time < day_start
    return night_start <= local_time < day_start


def _next_daytime_start(now: datetime, settings: Settings) -> datetime:
    local_now = now.astimezone()
    day_start = _parse_time(settings.poll_day_start)
    candidate = local_now.replace(
        hour=day_start.hour,
        minute=day_start.minute,
        second=0,
        microsecond=0,
    )
    if candidate <= local_now:
        candidate += timedelta(days=1)
    return candidate.astimezone(UTC)


def _parse_time(value: str) -> time:
    hour, minute = value.split(":", maxsplit=1)
    return time(hour=int(hour), minute=int(minute))


def _random_delta(min_minutes: int, max_minutes: int) -> timedelta:
    spread = max_minutes - min_minutes
    return timedelta(minutes=min_minutes + (secrets.randbelow(spread + 1) if spread else 0))
