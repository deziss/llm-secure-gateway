"""Tests for at-rest encryption of LLMBackend.api_key (services/secret_crypto)."""

import pytest
from sqlalchemy import text

from llm_gateway.services import secret_crypto
from llm_gateway.services.secret_crypto import (
    ENC_PREFIX,
    decrypt_secret,
    encrypt_secret,
    is_masked,
    mask_secret,
    upgrade_plaintext_keys,
)


@pytest.fixture(autouse=True)
def stable_fernet_key(monkeypatch):
    """Deterministic key for every test; reset the lazily-cached Fernet."""
    from cryptography.fernet import Fernet

    monkeypatch.setenv("GATEWAY_SECRET_KEY", Fernet.generate_key().decode())
    secret_crypto._fernet = None
    secret_crypto._ephemeral = False
    yield
    secret_crypto._fernet = None
    secret_crypto._ephemeral = False


# ---------------------------------------------------------------- helper level

def test_encrypt_decrypt_roundtrip():
    token = encrypt_secret("sk-super-secret-12345")
    assert token.startswith(ENC_PREFIX)
    assert "sk-super-secret" not in token
    assert decrypt_secret(token) == "sk-super-secret-12345"


def test_encrypt_is_idempotent_and_passes_through_empty():
    once = encrypt_secret("sk-abc")
    assert encrypt_secret(once) == once
    assert encrypt_secret("") == ""
    assert encrypt_secret(None) is None


def test_legacy_plaintext_passthrough():
    # Values without the prefix are legacy plaintext: returned unchanged.
    assert decrypt_secret("sk-legacy-plaintext-key") == "sk-legacy-plaintext-key"
    assert decrypt_secret("") == ""
    assert decrypt_secret(None) is None


def test_decrypt_invalid_token_raises_value_error():
    bogus = ENC_PREFIX + "not-a-real-fernet-token"
    with pytest.raises(ValueError):
        decrypt_secret(bogus)


def test_different_keys_cannot_decrypt(monkeypatch):
    token = encrypt_secret("sk-shared")
    from cryptography.fernet import Fernet

    monkeypatch.setenv("GATEWAY_SECRET_KEY", Fernet.generate_key().decode())
    secret_crypto._fernet = None
    with pytest.raises(ValueError):
        decrypt_secret(token)


# ------------------------------------------------------------------- masking

def test_mask_secret_display():
    assert mask_secret("sk-abcdef123456789wxyz") == "sk-...wxyz"
    assert mask_secret("short") == "****"
    assert mask_secret("12345678") == "****"
    assert mask_secret("123456789") == "123...6789"
    assert mask_secret("") == ""
    assert mask_secret(None) is None


def test_is_masked_detects_display_values():
    assert is_masked("****")
    assert is_masked("sk-...wxyz")
    assert is_masked("ab..." + "cdef")
    assert not is_masked(None)
    assert not is_masked("")
    # Real keys are long and contain no elision runs.
    assert not is_masked("sk-proj-abcdefghijklmnopqrstuvwxyz0123456789")
    # Real keys are long even if they contain dots; elision requires a mask shape.
    assert not is_masked("sk-abcdef123456789wxyz")


# ------------------------------------------------- model column (TypeDecorator)

async def test_model_column_encrypts_at_rest(db_session):
    from llm_gateway.models import BackendType, LLMBackend

    backend = LLMBackend(
        name="enc-test",
        base_url="http://localhost:11434",
        backend_type=BackendType.OPENAI,
        api_key="sk-real-secret-key-9999",
        models=[],
        allowed_endpoints=["*"],
    )
    db_session.add(backend)
    await db_session.commit()

    # Raw SELECT (bypassing the ORM type) must show the encrypted prefix.
    raw = (
        await db_session.execute(
            text("SELECT api_key FROM model_server WHERE name = 'enc-test'")
        )
    ).scalar()
    assert raw.startswith(ENC_PREFIX)
    assert "sk-real-secret-key-9999" not in raw

    # ORM read returns transparently-decrypted plaintext.
    db_session.expire_all()
    loaded = await db_session.get(LLMBackend, "enc-test")
    assert loaded.api_key == "sk-real-secret-key-9999"


async def test_model_column_null_api_key(db_session):
    from llm_gateway.models import BackendType, LLMBackend

    backend = LLMBackend(
        name="no-key",
        base_url="http://localhost:11434",
        backend_type=BackendType.OLLAMA,
        models=[],
        allowed_endpoints=["*"],
    )
    db_session.add(backend)
    await db_session.commit()
    raw = (
        await db_session.execute(
            text("SELECT api_key FROM model_server WHERE name = 'no-key'")
        )
    ).scalar()
    assert raw is None
    db_session.expire_all()
    loaded = await db_session.get(LLMBackend, "no-key")
    assert loaded.api_key is None


async def test_model_update_rotates_key(db_session):
    from llm_gateway.models import BackendType, LLMBackend

    backend = LLMBackend(
        name="rotate",
        base_url="http://x",
        backend_type=BackendType.OPENAI,
        api_key="sk-old-key-0000000000",
        models=[],
        allowed_endpoints=["*"],
    )
    db_session.add(backend)
    await db_session.commit()

    backend.api_key = "sk-new-key-1111111111"
    db_session.add(backend)
    await db_session.commit()
    db_session.expire_all()

    loaded = await db_session.get(LLMBackend, "rotate")
    assert loaded.api_key == "sk-new-key-1111111111"
    raw = (
        await db_session.execute(
            text("SELECT api_key FROM model_server WHERE name = 'rotate'")
        )
    ).scalar()
    assert raw.startswith(ENC_PREFIX)


# ------------------------------------------------------ legacy row upgrading

async def test_upgrade_plaintext_keys_upgrades_legacy_rows(db_session):
    from llm_gateway.models import BackendType, LLMBackend

    # Insert one legacy plaintext row the way old code did (raw SQL, no type).
    await db_session.execute(
        text(
            "INSERT INTO model_server (name, base_url, backend_type, api_key, "
            "models, fallback_urls, allowed_endpoints, translation_mode, "
            "normalize_thinking, weight, created_at) "
            "VALUES ('legacy', 'http://x', 'OPENAI', 'sk-legacy-plain-123456', "
            "'[]', '[]', '[\"*\"]', 'none', 0, 100, '2025-01-01 00:00:00')"
        )
    )
    # And one already-encrypted row that must be left alone.
    modern = LLMBackend(
        name="modern",
        base_url="http://y",
        backend_type=BackendType.OPENAI,
        api_key="sk-modern-9999999",
        models=[],
        allowed_endpoints=["*"],
    )
    db_session.add(modern)
    await db_session.commit()

    raw_before = (
        await db_session.execute(
            text("SELECT api_key FROM model_server WHERE name = 'legacy'")
        )
    ).scalar()
    assert raw_before == "sk-legacy-plain-123456"

    upgraded = await upgrade_plaintext_keys(db_session)
    assert upgraded == ["legacy"]

    raw_after = (
        await db_session.execute(
            text("SELECT api_key FROM model_server WHERE name = 'legacy'")
        )
    ).scalar()
    assert raw_after.startswith(ENC_PREFIX)

    # ORM read still returns plaintext after upgrade.
    db_session.expire_all()
    loaded = await db_session.get(LLMBackend, "legacy")
    assert loaded.api_key == "sk-legacy-plain-123456"

    # Second run is a no-op (already-encrypted rows are skipped).
    assert await upgrade_plaintext_keys(db_session) == []


# ------------------------------------------------------------- key resolution

def test_ephemeral_fallback_warns(monkeypatch, caplog):
    import logging

    for var in ("GATEWAY_SECRET_KEY", "ENCRYPTION_KEY", "SECRET_KEY", "AUTH_SECRET"):
        monkeypatch.delenv(var, raising=False)
    secret_crypto._fernet = None
    secret_crypto._ephemeral = False
    with caplog.at_level(logging.WARNING, logger="llm_gateway.services.secret_crypto"):
        roundtrip = decrypt_secret(encrypt_secret("sk-ephemeral"))
    assert roundtrip == "sk-ephemeral"
    assert secret_crypto._ephemeral is True
    assert any("GATEWAY_SECRET_KEY" in r.message for r in caplog.records)


def test_non_fernet_key_is_derived_and_stable(monkeypatch):
    monkeypatch.setenv("GATEWAY_SECRET_KEY", "just-a-passphrase-not-a-fernet-key")
    secret_crypto._fernet = None
    token = encrypt_secret("sk-derived")
    secret_crypto._fernet = None  # force re-resolution from the same secret
    assert decrypt_secret(token) == "sk-derived"
