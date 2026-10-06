"""Symmetric encryption for secrets at rest (Gmail refresh tokens) and the OAuth state cookie."""

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class CryptoNotConfiguredError(RuntimeError):
    pass


def _fernet() -> Fernet:
    key = get_settings().token_encryption_key
    if key is None or not key.get_secret_value():
        raise CryptoNotConfiguredError("TOKEN_ENCRYPTION_KEY is not set")
    try:
        return Fernet(key.get_secret_value().encode())
    except ValueError as exc:
        # The message would not include the key, but keep it generic anyway.
        raise CryptoNotConfiguredError("TOKEN_ENCRYPTION_KEY is not a valid Fernet key") from exc


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str, *, max_age_seconds: int | None = None) -> str | None:
    """Plaintext, or None when the ciphertext is invalid, tampered with, or older than `max_age_seconds`."""
    try:
        return _fernet().decrypt(ciphertext.encode(), ttl=max_age_seconds).decode()
    except InvalidToken:
        return None
