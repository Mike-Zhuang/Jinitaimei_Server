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


@lru_cache
def get_settings() -> Settings:
    return Settings()
