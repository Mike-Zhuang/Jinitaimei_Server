from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(default="development", alias="APP_ENV")
    app_base_url: str = Field(default="https://tjpush.mikezhuang.cn", alias="APP_BASE_URL")
    host: str = Field(default="127.0.0.1", alias="HOST")
    port: int = Field(default=31080, alias="PORT")

    database_path: str = Field(default="./data/jinitaimei-push.sqlite3", alias="DATABASE_PATH")

    smtp_host: str = Field(default="smtp.exmail.qq.com", alias="SMTP_HOST")
    smtp_port: int = Field(default=465, alias="SMTP_PORT")
    smtp_username: str = Field(default="", alias="SMTP_USERNAME")
    smtp_password: str = Field(default="", alias="SMTP_PASSWORD")
    smtp_from: str = Field(default="济你太美通知 <tjpush_admin@mikezhuang.cn>", alias="SMTP_FROM")

    admin_token: str = Field(default="", alias="ADMIN_TOKEN")
    credential_encryption_key: str = Field(default="", alias="CREDENTIAL_ENCRYPTION_KEY")

    poll_day_start: str = Field(default="07:00", alias="POLL_DAY_START")
    poll_night_start: str = Field(default="23:30", alias="POLL_NIGHT_START")
    poll_teaching_notice_day_min_minutes: int = Field(
        default=45, alias="POLL_TEACHING_NOTICE_DAY_MIN_MINUTES"
    )
    poll_teaching_notice_day_max_minutes: int = Field(
        default=90, alias="POLL_TEACHING_NOTICE_DAY_MAX_MINUTES"
    )
    poll_teaching_notice_night_min_minutes: int = Field(
        default=180, alias="POLL_TEACHING_NOTICE_NIGHT_MIN_MINUTES"
    )
    poll_teaching_notice_night_max_minutes: int = Field(
        default=360, alias="POLL_TEACHING_NOTICE_NIGHT_MAX_MINUTES"
    )
    poll_star_public_day_min_minutes: int = Field(
        default=60, alias="POLL_STAR_PUBLIC_DAY_MIN_MINUTES"
    )
    poll_star_public_day_max_minutes: int = Field(
        default=120, alias="POLL_STAR_PUBLIC_DAY_MAX_MINUTES"
    )
    poll_star_public_night_min_minutes: int = Field(
        default=240, alias="POLL_STAR_PUBLIC_NIGHT_MIN_MINUTES"
    )
    poll_star_public_night_max_minutes: int = Field(
        default=480, alias="POLL_STAR_PUBLIC_NIGHT_MAX_MINUTES"
    )
    poll_star_private_day_min_minutes: int = Field(
        default=180, alias="POLL_STAR_PRIVATE_DAY_MIN_MINUTES"
    )
    poll_star_private_day_max_minutes: int = Field(
        default=360, alias="POLL_STAR_PRIVATE_DAY_MAX_MINUTES"
    )
    poll_campus_card_day_min_minutes: int = Field(
        default=90, alias="POLL_CAMPUS_CARD_DAY_MIN_MINUTES"
    )
    poll_campus_card_day_max_minutes: int = Field(
        default=180, alias="POLL_CAMPUS_CARD_DAY_MAX_MINUTES"
    )
    poll_campus_card_night_min_minutes: int = Field(
        default=240, alias="POLL_CAMPUS_CARD_NIGHT_MIN_MINUTES"
    )
    poll_campus_card_night_max_minutes: int = Field(
        default=480, alias="POLL_CAMPUS_CARD_NIGHT_MAX_MINUTES"
    )
    poll_max_backoff_minutes: int = Field(default=720, alias="POLL_MAX_BACKOFF_MINUTES")


@lru_cache
def get_settings() -> Settings:
    return Settings()
