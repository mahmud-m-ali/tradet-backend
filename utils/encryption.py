"""
Field-level encryption for PII stored in the database.
INSA CSMS compliance — Technology pillar: encryption at rest.

Encrypts: full_name, phone, id_number (KYC)
Leaves:   email (used as login lookup key — treated as semi-public identifier)

Key is read from FIELD_ENCRYPTION_KEY env var (32-byte base64-urlsafe string).
If not set, a stable key is derived from SECRET_KEY so existing deployments
continue to work without manual key provisioning.
"""

import os
import base64
import logging
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

# Fernet tokens always start with this prefix — used to detect already-encrypted values
_FERNET_PREFIX = b'gAAAAA'


def _get_fernet() -> Fernet:
    """Return a Fernet instance using FIELD_ENCRYPTION_KEY or derive one from SECRET_KEY."""
    raw = os.getenv('FIELD_ENCRYPTION_KEY', '')
    if raw:
        try:
            key = raw.encode() if isinstance(raw, str) else raw
            return Fernet(key)
        except Exception:
            logger.warning("FIELD_ENCRYPTION_KEY is invalid — falling back to derived key")

    # Derive a stable 32-byte key from SECRET_KEY using SHA-256
    import hashlib
    secret = os.getenv('SECRET_KEY', 'tradet-dev-secret-key-change-in-production')
    digest = hashlib.sha256(secret.encode()).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


_fernet: Fernet | None = None


def _f() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = _get_fernet()
    return _fernet


def encrypt_field(value: str | None) -> str | None:
    """Encrypt a plaintext string field. Returns None if value is None."""
    if value is None:
        return None
    b = value.encode('utf-8')
    # Skip if already encrypted
    if b.startswith(_FERNET_PREFIX):
        return value
    return _f().encrypt(b).decode('utf-8')


def decrypt_field(value: str | None) -> str | None:
    """Decrypt an encrypted field. Returns the original value if it cannot be decrypted
    (handles plaintext values already in DB during migration)."""
    if value is None:
        return None
    b = value.encode('utf-8')
    if not b.startswith(_FERNET_PREFIX):
        # Plaintext — not yet encrypted (e.g. existing DB rows)
        return value
    try:
        return _f().decrypt(b).decode('utf-8')
    except InvalidToken:
        logger.error("Failed to decrypt field — returning masked value")
        return '***'


def generate_key() -> str:
    """Generate a new random Fernet key. Run once and store in FIELD_ENCRYPTION_KEY."""
    return Fernet.generate_key().decode('utf-8')
