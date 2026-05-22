from email.message import EmailMessage

import aiosmtplib

from app.config import get_settings


async def send_email(to_address: str, subject: str, text: str) -> None:
    settings = get_settings()
    if not settings.smtp_username or not settings.smtp_password:
        raise RuntimeError("SMTP_USERNAME / SMTP_PASSWORD 尚未配置")

    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to_address
    message["Subject"] = subject
    message.set_content(text)

    await aiosmtplib.send(
        message,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_username,
        password=settings.smtp_password,
        use_tls=True,
        timeout=20,
    )
