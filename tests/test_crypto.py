import pytest

from leadgen.send.crypto import DecryptionError, TokenCipher, generate_key


def test_round_trip() -> None:
    cipher = TokenCipher(generate_key())
    ciphertext = cipher.encrypt("refresh-token-value")
    assert ciphertext != "refresh-token-value"
    assert cipher.decrypt(ciphertext) == "refresh-token-value"


def test_wrong_key_fails_to_decrypt() -> None:
    ciphertext = TokenCipher(generate_key()).encrypt("secret")
    with pytest.raises(DecryptionError):
        TokenCipher(generate_key()).decrypt(ciphertext)


def test_same_plaintext_produces_different_ciphertext() -> None:
    cipher = TokenCipher(generate_key())
    assert cipher.encrypt("same") != cipher.encrypt("same")
