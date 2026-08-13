from __future__ import annotations

import os
import threading

from cryptography.fernet import Fernet, InvalidToken


class CredentialEncryptionError(RuntimeError):
    """Raised when credential encryption cannot safely proceed."""


_fernet_lock = threading.Lock()
_fernet: Fernet | None = None


def _load_key() -> bytes:
    raw = os.environ.get("CREDENTIAL_ENCRYPTION_KEY", "").strip()
    if not raw:
        raise CredentialEncryptionError(
            "CREDENTIAL_ENCRYPTION_KEY is not set. Generate one with "
            "`python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"`."
        )
    return raw.encode("utf-8")


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is not None:
        return _fernet
    with _fernet_lock:
        if _fernet is None:
            try:
                _fernet = Fernet(_load_key())
            except ValueError as exc:
                raise CredentialEncryptionError("CREDENTIAL_ENCRYPTION_KEY is not a valid Fernet key.") from exc
        return _fernet


def encrypt_secret(value: str) -> str:
    if not value:
        raise CredentialEncryptionError("Refusing to encrypt an empty credential value.")
    return _get_fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str) -> str:
    try:
        return _get_fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise CredentialEncryptionError(
            "Stored credential could not be decrypted with the configured CREDENTIAL_ENCRYPTION_KEY."
        ) from exc


def mask_secret(value: str) -> str:
    if len(value) < 4:
        return "••••"
    return f"••••{value[-4:]}"


# Backward-compatible public names used by the tests and older callers.
encrypt_value = encrypt_secret
decrypt_value = decrypt_secret
mask_value = mask_secret


def reset_for_testing() -> None:
    global _fernet
    with _fernet_lock:
        _fernet = None
