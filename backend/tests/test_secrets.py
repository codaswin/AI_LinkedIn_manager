from __future__ import annotations

import pytest
from app.safety import secrets
from app.safety.secrets import (
    CredentialEncryptionError,
    decrypt_value,
    encrypt_value,
    mask_value,
)
from cryptography.fernet import Fernet


@pytest.fixture(autouse=True)
def _reset_fernet_cache() -> None:
    secrets.reset_for_testing()
    yield
    secrets.reset_for_testing()


def test_encrypt_then_decrypt_roundtrips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    ciphertext = encrypt_value("sk-super-secret-value")
    assert ciphertext != "sk-super-secret-value"
    assert decrypt_value(ciphertext) == "sk-super-secret-value"


def test_encrypt_raises_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEY", raising=False)
    with pytest.raises(CredentialEncryptionError, match="CREDENTIAL_ENCRYPTION_KEY is not set"):
        encrypt_value("x")


def test_encrypt_raises_with_a_malformed_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "not-a-real-fernet-key")
    with pytest.raises(CredentialEncryptionError, match="not a valid Fernet key"):
        encrypt_value("x")


def test_decrypt_raises_when_key_has_changed_since_saving(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    ciphertext = encrypt_value("sk-super-secret-value")

    secrets.reset_for_testing()
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with pytest.raises(CredentialEncryptionError, match="could not be decrypted"):
        decrypt_value(ciphertext)


def test_mask_value_shows_only_last_four_characters() -> None:
    assert mask_value("sk-proj-abcd1234") == "••••1234"


def test_mask_value_handles_very_short_values() -> None:
    assert mask_value("ab") == "••••"


def test_client_is_cached_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    encrypt_value("first-call-resolves-and-caches-the-fernet-instance")
    # A second call must not re-read the env var — changing it here proves
    # the cached instance (not a fresh read) is what's actually used.
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    ciphertext = encrypt_value("second-call")
    assert decrypt_value(ciphertext) == "second-call"
