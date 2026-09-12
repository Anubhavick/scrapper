"""Encryption for `mailboxes.oauth_refresh_token_encrypted`.

Refresh tokens are long-lived credentials for someone's real Gmail account —
CLAUDE.md is explicit that the encrypted form is the only one that column
should ever hold. Fernet (symmetric, authenticated) is enough here: this key
never leaves the app's own environment, there's no key-rotation-across-
services requirement, and it fails loudly (raises) on tampering or the wrong
key rather than silently returning garbage.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

__all__ = ["TokenCipher", "DecryptionError", "generate_key"]


class DecryptionError(Exception):
    pass


def generate_key() -> str:
    """Generate a value suitable for the TOKEN_ENCRYPTION_KEY env var."""
    return Fernet.generate_key().decode("ascii")


class TokenCipher:
    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode("ascii"))

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise DecryptionError(
                "refresh token could not be decrypted — wrong TOKEN_ENCRYPTION_KEY, "
                "or the stored value was corrupted/tampered with"
            ) from exc
