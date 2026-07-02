"""
services/encryption_service.py — RiskLens v2
==============================================
AES-256 encryption for user AI API keys.

We use Fernet symmetric encryption from the `cryptography` library.
Fernet = AES-128-CBC + HMAC-SHA256, with authenticated encryption —
it guarantees both confidentiality AND integrity. The key is 32 random
bytes (256 bits) base64-encoded, stored in the ENCRYPTION_KEY env var.

Key generation (run once, store in Render env):
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Why not AES-256-GCM directly: Fernet is simpler to use correctly,
handles IV/nonce generation and authentication tag automatically,
and is battle-tested. For key storage in an env var, this is
the right level of complexity.

Security model:
  - ENCRYPTION_KEY never touches the DB
  - Encrypted API keys (ai_api_key_enc) are stored in Supabase
  - Decryption only happens server-side, in memory, when a request
    needs to call the user's AI provider
  - Even if the Supabase DB is leaked, API keys are not exposed
"""

import os
from typing import Optional
from cryptography.fernet import Fernet, InvalidToken

_RAW_KEY = os.environ.get("ENCRYPTION_KEY", "")

if not _RAW_KEY:
    raise RuntimeError(
        "ENCRYPTION_KEY environment variable is not set. "
        "Generate one with: python -c \"from cryptography.fernet import Fernet; "
        "print(Fernet.generate_key().decode())\""
    )

try:
    _FERNET = Fernet(_RAW_KEY.encode() if isinstance(_RAW_KEY, str) else _RAW_KEY)
except Exception as e:
    raise RuntimeError(
        f"ENCRYPTION_KEY is invalid: {e}. "
        "It must be a 32-byte URL-safe base64-encoded string."
    ) from e


def encrypt_api_key(plaintext_key: str) -> str:
    """
    Encrypt a plaintext API key and return the encrypted string
    (URL-safe base64, safe to store in a TEXT column).

    Args:
        plaintext_key: The raw API key from the user (e.g. 'sk-ant-...')

    Returns:
        Encrypted token as a UTF-8 string, safe for DB storage.
    """
    if not plaintext_key or not plaintext_key.strip():
        raise ValueError("API key cannot be empty")
    return _FERNET.encrypt(plaintext_key.strip().encode()).decode()


def decrypt_api_key(encrypted_key: str) -> str:
    """
    Decrypt an encrypted API key back to plaintext.

    Args:
        encrypted_key: The value stored in ai_api_key_enc column.

    Returns:
        The original plaintext API key.

    Raises:
        ValueError: If decryption fails (key was tampered or wrong env key).
    """
    if not encrypted_key:
        raise ValueError("No encrypted key provided")
    try:
        return _FERNET.decrypt(encrypted_key.encode()).decode()
    except InvalidToken as e:
        raise ValueError(
            "Failed to decrypt API key — the ENCRYPTION_KEY may have changed "
            "or the stored value is corrupted."
        ) from e


def has_valid_key(encrypted_key: Optional[str]) -> bool:
    """
    Returns True if the stored encrypted key is non-empty and decryptable.
    Used for checking whether a user has connected their AI.
    Does NOT return the decrypted value — use decrypt_api_key() for that.
    """
    if not encrypted_key:
        return False
    try:
        _FERNET.decrypt(encrypted_key.encode())
        return True
    except InvalidToken:
        return False
