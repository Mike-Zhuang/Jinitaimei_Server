from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from app.config import get_settings


@asynccontextmanager
async def open_database() -> AsyncIterator[aiosqlite.Connection]:
    settings = get_settings()
    db_path = Path(settings.database_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(db_path)
    db.row_factory = aiosqlite.Row
    try:
        yield db
    finally:
        await db.close()


async def init_database() -> None:
    async with open_database() as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                mail_push_enabled INTEGER NOT NULL DEFAULT 0,
                teaching_notice_enabled INTEGER NOT NULL DEFAULT 1,
                star_new_activity_enabled INTEGER NOT NULL DEFAULT 1,
                star_registration_enabled INTEGER NOT NULL DEFAULT 1,
                selected_star_module_codes TEXT NOT NULL DEFAULT '[]',
                followed_star_activity_ids TEXT NOT NULL DEFAULT '[]',
                last_seen_teaching_notice_id INTEGER,
                last_seen_teaching_notice_time TEXT,
                last_seen_star_activity_ids TEXT NOT NULL DEFAULT '[]',
                last_seen_star_registration_open_ids TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS credential_vault (
                subscription_id INTEGER PRIMARY KEY,
                tongji_username TEXT NOT NULL,
                encrypted_tongji_password TEXT NOT NULL,
                encrypted_cookie TEXT,
                encrypted_x_token TEXT,
                encrypted_star_token TEXT,
                credential_version INTEGER NOT NULL DEFAULT 1,
                last_login_at TEXT,
                last_success_at TEXT,
                last_failure_reason TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(subscription_id) REFERENCES subscriptions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS notification_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subscription_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                external_id TEXT NOT NULL,
                title TEXT NOT NULL,
                sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(subscription_id, event_type, external_id),
                FOREIGN KEY(subscription_id) REFERENCES subscriptions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS notification_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS polling_state (
                task_name TEXT PRIMARY KEY,
                last_run_at TEXT,
                next_allowed_at TEXT,
                failure_count INTEGER NOT NULL DEFAULT 0,
                last_failure_reason TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        await _ensure_column(db, "subscriptions", "mail_push_enabled", "INTEGER NOT NULL DEFAULT 0")
        await _ensure_column(
            db,
            "subscriptions",
            "followed_star_activity_ids",
            "TEXT NOT NULL DEFAULT '[]'",
        )
        await _ensure_column(
            db,
            "subscriptions",
            "last_seen_teaching_notice_id",
            "INTEGER",
        )
        await _ensure_column(
            db,
            "subscriptions",
            "last_seen_teaching_notice_time",
            "TEXT",
        )
        await _ensure_column(
            db,
            "subscriptions",
            "last_seen_star_activity_ids",
            "TEXT NOT NULL DEFAULT '[]'",
        )
        await _ensure_column(
            db,
            "subscriptions",
            "last_seen_star_registration_open_ids",
            "TEXT NOT NULL DEFAULT '[]'",
        )
        await db.commit()


async def _ensure_column(
    db: aiosqlite.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    cursor = await db.execute(f"PRAGMA table_info({table})")
    columns = {row["name"] for row in await cursor.fetchall()}
    if column not in columns:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
