"""
Credential encryption helpers.

Encrypts sensitive auth fields with Fernet (AES-128-CBC + HMAC).
The configured ENCRYPTION_KEY is hashed to derive a proper 32-byte
URL-safe base64 Fernet key, so any string can be used as input.

Encrypted values are tagged with the prefix `enc:v1:` so we can:
  - Distinguish encrypted from plain values during migration
  - Detect key mismatch when decryption fails (raise a clear error)
"""

import base64
import hashlib
import json
import logging
from typing import Any, Dict, Optional

from cryptography.fernet import Fernet, InvalidToken

from ..config import settings

logger = logging.getLogger(__name__)

# Sensitive fields within scrape_config.auth that should be encrypted at rest
SENSITIVE_AUTH_FIELDS = ("username", "password", "token", "cookies", "session_data")
ENCRYPTED_PREFIX = "enc:v1:"


class CredentialDecryptError(Exception):
    """Raised when an encrypted credential cannot be decrypted (likely key mismatch)."""


def _derive_fernet_key(master: str) -> bytes:
    """Derive a valid Fernet key from any input string."""
    digest = hashlib.sha256(master.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _fernet() -> Fernet:
    return Fernet(_derive_fernet_key(settings.ENCRYPTION_KEY))


def _encrypt_value(value: Any) -> str:
    """Encrypt any JSON-serializable value, returning a tagged string."""
    serialized = json.dumps(value, default=str)
    token = _fernet().encrypt(serialized.encode("utf-8")).decode("utf-8")
    return f"{ENCRYPTED_PREFIX}{token}"


def _decrypt_value(value: str) -> Any:
    """Decrypt a tagged string back to its original JSON-serializable value."""
    if not isinstance(value, str) or not value.startswith(ENCRYPTED_PREFIX):
        # Not encrypted — return as-is (legacy data path)
        return value
    token = value[len(ENCRYPTED_PREFIX):]
    try:
        plain = _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as e:
        raise CredentialDecryptError(
            "Failed to decrypt credential. The ENCRYPTION_KEY appears to have changed "
            "since this job was created. Either restore the original key or recreate the job."
        ) from e
    return json.loads(plain)


def encrypt_auth(auth: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return a copy of auth with sensitive fields encrypted.

    Skips fields that are None, empty, or already encrypted.
    Non-sensitive fields (like 'method') are left as-is.
    """
    if not auth:
        return auth
    out = dict(auth)
    for field in SENSITIVE_AUTH_FIELDS:
        val = out.get(field)
        if val is None or val == "" or val == {}:
            continue
        if isinstance(val, str) and val.startswith(ENCRYPTED_PREFIX):
            continue  # already encrypted
        out[field] = _encrypt_value(val)
    return out


def decrypt_auth(auth: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return a copy of auth with sensitive fields decrypted.

    Raises CredentialDecryptError if a tagged ciphertext fails to decrypt.
    """
    if not auth:
        return auth
    out = dict(auth)
    for field in SENSITIVE_AUTH_FIELDS:
        val = out.get(field)
        if val is None:
            continue
        if isinstance(val, str) and val.startswith(ENCRYPTED_PREFIX):
            out[field] = _decrypt_value(val)
    return out


def encrypt_scrape_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of scrape_config with auth fields encrypted."""
    if not config:
        return config
    out = dict(config)
    if "auth" in out:
        out["auth"] = encrypt_auth(out["auth"])
    return out


def decrypt_scrape_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of scrape_config with auth fields decrypted."""
    if not config:
        return config
    out = dict(config)
    if "auth" in out:
        out["auth"] = decrypt_auth(out["auth"])
    return out
