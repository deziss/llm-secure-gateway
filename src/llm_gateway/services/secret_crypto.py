"""At-rest encryption helpers for LLM backend API keys.

Encrypted values are stored inline in the existing column with an
``enc:v1:`` prefix, so legacy plaintext rows keep reading unchanged (they are
simply passed through) and no schema migration is required.

Key resolution order, evaluated lazily on first use:
  1. ``GATEWAY_SECRET_KEY``  -- a dedicated Fernet key (recommended).
  2. ``ENCRYPTION_KEY``      -- the app-wide encryption secret (preferred app secret).
  3. ``AUTH_SECRET``         -- any other stable app secret, SHA-256 derived.
  4. An ephemeral per-process key, persisting nothing: keys encrypted in one
     process cannot be decrypted after a restart.  A WARNING explains this.
"""

import base64
import hashlib
import logging
import os

from cryptography.fernet import Fernet, InvalidToken


logger = logging.getLogger(__name__)

ENC_PREFIX = "enc:v1:"


def _derive_fernet_key(secret: str) -> str:
    """Derive a urlsafe-base64 32-byte Fernet key from an arbitrary secret."""
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()).decode()


_fernet: Fernet | None = None
_ephemeral = False


def _get_fernet() -> Fernet:
    global _fernet, _ephemeral
    if _fernet is not None:
        return _fernet
    key = os.getenv("GATEWAY_SECRET_KEY", "") or os.getenv("ENCRYPTION_KEY", "") or os.getenv("SECRET_KEY", "")
    if not key:
        auth_secret = os.getenv("AUTH_SECRET", "")
        if auth_secret:
            key = _derive_fernet_key(auth_secret)
        else:
            key = Fernet.generate_key().decode()
            _ephemeral = True
            logger.warning(
                "GATEWAY_SECRET_KEY is not set; generated an ephemeral key for "
                "backend API-key encryption. Encrypted keys will NOT survive a "
                "restart. Set GATEWAY_SECRET_KEY to a Fernet key "
                "(python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\")."
            )
    elif not _looks_like_fernet_key(key):
        key = _derive_fernet_key(key)
    try:
        _fernet = Fernet(key.encode())
    except Exception as exc:  # malformed key after any derivation attempt
        logger.error(
            "GATEWAY_SECRET_KEY/ENCRYPTION_KEY is not a usable Fernet key (%s); "
            "deriving one from it instead. Existing enc:v1: values encrypted with "
            "a different derivation will fail to decrypt.",
            exc,
        )
        _fernet = Fernet(_derive_fernet_key(key).encode())
    return _fernet


def _looks_like_fernet_key(value: str) -> bool:
    try:
        raw = base64.urlsafe_b64decode(value.encode())
    except Exception:
        return False
    return len(raw) == 32


def encrypt_secret(plaintext: str) -> str:
    """Encrypt *plaintext*, returning ``enc:v1:<fernet-token>``.

    Empty/None values and values already carrying the prefix pass through
    unchanged (idempotent).
    """
    if not plaintext or plaintext.startswith(ENC_PREFIX):
        return plaintext
    return ENC_PREFIX + _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(stored: str) -> str:
    """Decrypt a stored value.

    Values without the ``enc:v1:`` prefix are legacy plaintext and are
    returned unchanged.  Raises ``ValueError`` (never HTTPException) when a
    prefixed token cannot be decrypted; callers decide how to respond.
    """
    if not stored or not stored.startswith(ENC_PREFIX):
        return stored
    try:
        return _get_fernet().decrypt(stored[len(ENC_PREFIX):].encode()).decode()
    except InvalidToken as exc:
        logger.error(
            "Failed to decrypt an enc:v1: secret; GATEWAY_SECRET_KEY may have "
            "changed or the value was encrypted with a different key."
        )
        raise ValueError("Unable to decrypt stored secret") from exc


def is_masked(value) -> bool:
    """True for redacted display placeholders such as ``****`` or ``sk1...wxyz``.

    These are values our GET endpoints return; if a client echoes one back on
    update it must be treated as 'no change', never written to the DB.
    Detection: values made only of ``*`` (<=16 chars), or values containing an
    elision run (``...`` / four-or-more ``*``) that are short enough to be a
    mask we generated.  Real API keys do not contain such runs.
    """
    if not isinstance(value, str):
        return False
    value = value.strip()
    if not value:
        return False
    if len(value) <= 16 and all(ch == "*" for ch in value):
        return True
    if len(value) <= 24 and ("..." in value or "****" in value):
        return True
    return False


def mask_secret(plaintext) -> str:
    """Redact a secret for display: keep first 3 + last 4 chars when len > 8."""
    if not plaintext:
        return "" if plaintext == "" else plaintext
    if len(plaintext) > 8:
        return plaintext[:3] + "..." + plaintext[-4:]
    return "****"


# NOTE: the SQLAlchemy TypeDecorator used by LLMBackend.api_key lives in
# models.py (EncryptedString) to avoid a circular import; it delegates to
# encrypt_secret/decrypt_secret below.


async def upgrade_plaintext_keys(session) -> list:
    """Re-save every ``model_server`` row whose api_key is still plaintext.

    The column's EncryptedString type encrypts on the next UPDATE, so
    rewriting each offending row upgrades it in place; the UPDATE reuses the
    plaintext value read from the DB.  Reads use raw SQL (untyped) so that
    rows already encrypted with a rotated key never get decrypted -- they are
    filtered out by prefix instead.  Requires an async session.  Returns the
    names of upgraded rows.
    """
    from sqlalchemy import text as sa_text, update

    from ..models import LLMBackend

    result = await session.execute(sa_text("SELECT name, api_key FROM model_server"))
    legacy = [
        (name, key)
        for name, key in result.all()
        if key and not key.startswith(ENC_PREFIX)
    ]

    upgraded = []
    for name, stored in legacy:
        # The bind processor of EncryptedString encrypts as this UPDATE runs.
        await session.execute(
            update(LLMBackend).where(LLMBackend.name == name).values(api_key=stored)
        )
        upgraded.append(name)
    if upgraded:
        await session.commit()
        # Drop the stale (plaintext-cached) copies held by ConfigService.
        try:
            from .config_service import _backends_cache

            _backends_cache.clear()
        except Exception:
            pass
    logger.info("Upgraded %d backend api_key(s) to encrypted storage", len(upgraded))
    return upgraded
