import json
from dataclasses import dataclass
from datetime import UTC, datetime

from app.database import open_database
from app.schemas import CredentialRequest, SubscriptionRequest
from app.security import encrypt_secret


@dataclass(frozen=True)
class SubscriptionRecord:
    id: int
    email: str
    mail_push_enabled: bool
    teaching_notice_enabled: bool
    star_new_activity_enabled: bool
    star_registration_enabled: bool
    selected_star_module_codes: list[str]
    last_seen_teaching_notice_id: int | None
    last_seen_teaching_notice_time: str | None
    last_seen_star_activity_ids: list[str]
    tongji_username: str | None
    encrypted_tongji_password: str | None


@dataclass(frozen=True)
class PollingState:
    task_name: str
    last_run_at: datetime | None
    next_allowed_at: datetime | None
    failure_count: int


async def upsert_subscription(payload: SubscriptionRequest) -> int:
    module_codes = json.dumps(payload.selected_star_module_codes, ensure_ascii=False)
    async with open_database() as db:
        cursor = await db.execute(
            """
            INSERT INTO subscriptions (
                email,
                mail_push_enabled,
                teaching_notice_enabled,
                star_new_activity_enabled,
                star_registration_enabled,
                selected_star_module_codes
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET
                mail_push_enabled = excluded.mail_push_enabled,
                teaching_notice_enabled = excluded.teaching_notice_enabled,
                star_new_activity_enabled = excluded.star_new_activity_enabled,
                star_registration_enabled = excluded.star_registration_enabled,
                selected_star_module_codes = excluded.selected_star_module_codes,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                str(payload.email),
                int(payload.mail_push_enabled),
                int(payload.teaching_notice_enabled),
                int(payload.star_new_activity_enabled),
                int(payload.star_registration_enabled),
                module_codes,
            ),
        )
        await db.commit()
        if cursor.lastrowid:
            return int(cursor.lastrowid)
        existing = await db.execute(
            "SELECT id FROM subscriptions WHERE email = ?",
            (str(payload.email),),
        )
        row = await existing.fetchone()
        return int(row["id"])


async def save_credentials(payload: CredentialRequest) -> None:
    encrypted_password = encrypt_secret(payload.tongji_password)
    async with open_database() as db:
        cursor = await db.execute(
            "SELECT id FROM subscriptions WHERE email = ?",
            (str(payload.email),),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ValueError("订阅不存在，请先保存邮箱和通知偏好")
        subscription_id = int(row["id"])
        await db.execute(
            """
            INSERT INTO credential_vault (
                subscription_id,
                tongji_username,
                encrypted_tongji_password,
                credential_version,
                updated_at
            ) VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP)
            ON CONFLICT(subscription_id) DO UPDATE SET
                tongji_username = excluded.tongji_username,
                encrypted_tongji_password = excluded.encrypted_tongji_password,
                credential_version = credential_version + 1,
                last_failure_reason = NULL,
                updated_at = CURRENT_TIMESTAMP
            """,
            (subscription_id, payload.tongji_username, encrypted_password),
        )
        await db.execute(
            """
            UPDATE subscriptions
            SET mail_push_enabled = 1, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (subscription_id,),
        )
        await db.commit()


async def delete_subscription(email: str) -> bool:
    async with open_database() as db:
        cursor = await db.execute("SELECT id FROM subscriptions WHERE email = ?", (email,))
        row = await cursor.fetchone()
        if row is None:
            return False
        subscription_id = int(row["id"])
        await db.execute(
            "DELETE FROM notification_events WHERE subscription_id = ?",
            (subscription_id,),
        )
        await db.execute(
            "DELETE FROM credential_vault WHERE subscription_id = ?",
            (subscription_id,),
        )
        await db.execute("DELETE FROM subscriptions WHERE id = ?", (subscription_id,))
        await db.commit()
        return True


async def list_active_subscriptions() -> list[SubscriptionRecord]:
    async with open_database() as db:
        cursor = await db.execute(
            """
            SELECT
                s.*,
                c.tongji_username,
                c.encrypted_tongji_password
            FROM subscriptions s
            LEFT JOIN credential_vault c ON c.subscription_id = s.id
            WHERE s.mail_push_enabled = 1
            ORDER BY s.id ASC
            """
        )
        rows = await cursor.fetchall()
    return [_subscription_from_row(row) for row in rows]


async def mark_login_failure(subscription_id: int, reason: str) -> None:
    async with open_database() as db:
        await db.execute(
            """
            UPDATE credential_vault
            SET last_failure_reason = ?, updated_at = CURRENT_TIMESTAMP
            WHERE subscription_id = ?
            """,
            (reason[:256], subscription_id),
        )
        await db.commit()


async def mark_login_success(subscription_id: int) -> None:
    async with open_database() as db:
        await db.execute(
            """
            UPDATE credential_vault
            SET last_login_at = CURRENT_TIMESTAMP,
                last_success_at = CURRENT_TIMESTAMP,
                last_failure_reason = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE subscription_id = ?
            """,
            (subscription_id,),
        )
        await db.commit()


async def update_teaching_notice_baseline(
    subscription_id: int,
    notice_id: int,
    publish_time: str | None,
) -> None:
    async with open_database() as db:
        await db.execute(
            """
            UPDATE subscriptions
            SET last_seen_teaching_notice_id = ?,
                last_seen_teaching_notice_time = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (notice_id, publish_time, subscription_id),
        )
        await db.commit()


async def update_star_activity_baseline(
    subscription_id: int,
    activity_ids: list[str],
) -> None:
    # 只保留最近一批活动 ID，防止 JSON 字段无限增长。
    compact_ids = list(dict.fromkeys(activity_ids))[:300]
    async with open_database() as db:
        await db.execute(
            """
            UPDATE subscriptions
            SET last_seen_star_activity_ids = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (json.dumps(compact_ids, ensure_ascii=False), subscription_id),
        )
        await db.commit()


async def record_notification_event(
    subscription_id: int,
    event_type: str,
    external_id: str,
    title: str,
) -> bool:
    async with open_database() as db:
        cursor = await db.execute(
            """
            INSERT OR IGNORE INTO notification_events (
                subscription_id,
                event_type,
                external_id,
                title
            ) VALUES (?, ?, ?, ?)
            """,
            (subscription_id, event_type, external_id, title),
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_polling_state(task_name: str) -> PollingState | None:
    async with open_database() as db:
        cursor = await db.execute(
            "SELECT * FROM polling_state WHERE task_name = ?",
            (task_name,),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    return PollingState(
        task_name=row["task_name"],
        last_run_at=_parse_datetime(row["last_run_at"]),
        next_allowed_at=_parse_datetime(row["next_allowed_at"]),
        failure_count=int(row["failure_count"] or 0),
    )


async def upsert_polling_state(
    task_name: str,
    next_allowed_at: datetime,
    failure_count: int = 0,
    failure_reason: str | None = None,
) -> None:
    now = datetime.now(UTC).isoformat()
    async with open_database() as db:
        await db.execute(
            """
            INSERT INTO polling_state (
                task_name,
                last_run_at,
                next_allowed_at,
                failure_count,
                last_failure_reason,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(task_name) DO UPDATE SET
                last_run_at = excluded.last_run_at,
                next_allowed_at = excluded.next_allowed_at,
                failure_count = excluded.failure_count,
                last_failure_reason = excluded.last_failure_reason,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                task_name,
                now,
                next_allowed_at.astimezone(UTC).isoformat(),
                failure_count,
                failure_reason,
            ),
        )
        await db.commit()


def _subscription_from_row(row) -> SubscriptionRecord:
    return SubscriptionRecord(
        id=int(row["id"]),
        email=row["email"],
        mail_push_enabled=bool(row["mail_push_enabled"]),
        teaching_notice_enabled=bool(row["teaching_notice_enabled"]),
        star_new_activity_enabled=bool(row["star_new_activity_enabled"]),
        star_registration_enabled=bool(row["star_registration_enabled"]),
        selected_star_module_codes=json.loads(row["selected_star_module_codes"] or "[]"),
        last_seen_teaching_notice_id=row["last_seen_teaching_notice_id"],
        last_seen_teaching_notice_time=row["last_seen_teaching_notice_time"],
        last_seen_star_activity_ids=json.loads(row["last_seen_star_activity_ids"] or "[]"),
        tongji_username=row["tongji_username"],
        encrypted_tongji_password=row["encrypted_tongji_password"],
    )


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed
