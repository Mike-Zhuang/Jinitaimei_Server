from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


def generate_encryption_key() -> str:
    return Fernet.generate_key().decode("utf-8")


def _fernet() -> Fernet:
    key = get_settings().credential_encryption_key
    if not key:
        raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY 尚未配置")
    return Fernet(key.encode("utf-8"))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("凭据解密失败，请检查 CREDENTIAL_ENCRYPTION_KEY") from exc


def mask_email(email: str) -> str:
    name, _, domain = email.partition("@")
    if not domain:
        return "***"
    if len(name) <= 2:
        return f"{name[:1]}***@{domain}"
    return f"{name[:2]}***@{domain}"
