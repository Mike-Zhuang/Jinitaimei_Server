import json

from app.database import open_database
from app.schemas import SubscriptionRequest


async def upsert_subscription(payload: SubscriptionRequest) -> None:
    module_codes = json.dumps(payload.selected_star_module_codes, ensure_ascii=False)
    async with open_database() as db:
        await db.execute(
            """
            INSERT INTO subscriptions (
                email,
                teaching_notice_enabled,
                star_new_activity_enabled,
                star_registration_enabled,
                selected_star_module_codes
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET
                teaching_notice_enabled = excluded.teaching_notice_enabled,
                star_new_activity_enabled = excluded.star_new_activity_enabled,
                star_registration_enabled = excluded.star_registration_enabled,
                selected_star_module_codes = excluded.selected_star_module_codes,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                str(payload.email),
                int(payload.teaching_notice_enabled),
                int(payload.star_new_activity_enabled),
                int(payload.star_registration_enabled),
                module_codes,
            ),
        )
        await db.commit()
