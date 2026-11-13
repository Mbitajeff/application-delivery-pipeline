"""
Unit tests for core/security.py

Tests JWT creation, verification, password hashing, and error cases.
These tests run without any HTTP layer.
"""

from datetime import timedelta

import pytest

from app.core.security import (
    TokenError,
    TokenExpiredError,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


class TestPasswordHashing:
    """Tests for bcrypt password hashing."""

    def test_hash_is_not_plaintext(self) -> None:
        """Hashed password must not equal the plain password."""
        plain = "mysecretpassword1"
        hashed = hash_password(plain)
        assert hashed != plain

    def test_hash_includes_bcrypt_prefix(self) -> None:
        """bcrypt hashes always start with $2b$."""
        hashed = hash_password("password123")
        assert hashed.startswith("$2b$")

    def test_verify_correct_password_returns_true(self) -> None:
        plain = "correctpassword1"
        hashed = hash_password(plain)
        assert verify_password(plain, hashed) is True

    def test_verify_wrong_password_returns_false(self) -> None:
        hashed = hash_password("correctpassword1")
        assert verify_password("wrongpassword1", hashed) is False

    def test_same_password_produces_different_hashes(self) -> None:
        """bcrypt includes a random salt — same input, different outputs."""
        plain = "samepassword1"
        hash1 = hash_password(plain)
        hash2 = hash_password(plain)
        assert hash1 != hash2

    def test_both_hashes_verify_correctly(self) -> None:
        plain = "samepassword1"
        hash1 = hash_password(plain)
        hash2 = hash_password(plain)
        assert verify_password(plain, hash1) is True
        assert verify_password(plain, hash2) is True

    def test_empty_password_hashes_successfully(self) -> None:
        """Empty passwords are technically valid at the hashing level.
        Input validation should reject them before reaching here."""
        hashed = hash_password("")
        assert verify_password("", hashed) is True


class TestJWTTokens:
    """Tests for JWT creation and verification."""

    def test_create_token_returns_string(self) -> None:
        token = create_access_token("testuser")
        assert isinstance(token, str)
        assert len(token) > 0

    def test_token_has_three_parts(self) -> None:
        """JWTs are always header.payload.signature"""
        token = create_access_token("testuser")
        parts = token.split(".")
        assert len(parts) == 3

    def test_decode_valid_token_returns_claims(self) -> None:
        token = create_access_token("alice")
        payload = decode_access_token(token)
        assert payload["sub"] == "alice"

    def test_decoded_token_contains_exp_claim(self) -> None:
        token = create_access_token("alice")
        payload = decode_access_token(token)
        assert "exp" in payload

    def test_decoded_token_contains_iat_claim(self) -> None:
        token = create_access_token("alice")
        payload = decode_access_token(token)
        assert "iat" in payload

    def test_extra_claims_included_in_token(self) -> None:
        token = create_access_token("alice", extra_claims={"role": "admin"})
        payload = decode_access_token(token)
        assert payload["role"] == "admin"

    def test_reserved_claims_cannot_be_overridden(self) -> None:
        """extra_claims must not override sub, iat, or exp."""
        token = create_access_token(
            "alice",
            extra_claims={"sub": "mallory", "exp": 0},
        )
        payload = decode_access_token(token)
        # sub should still be alice, not mallory
        assert payload["sub"] == "alice"
        # exp should not be 0 (token would be immediately expired)
        assert payload["exp"] > 0

    def test_expired_token_raises_token_expired_error(self) -> None:
        """Tokens with negative expiry should raise TokenExpiredError."""
        token = create_access_token(
            "alice",
            expires_delta=timedelta(seconds=-1),
        )
        with pytest.raises(TokenExpiredError):
            decode_access_token(token)

    def test_malformed_token_raises_token_error(self) -> None:
        with pytest.raises(TokenError):
            decode_access_token("not.a.valid.jwt")

    def test_tampered_token_raises_token_error(self) -> None:
        """Modifying any part of the JWT should invalidate the signature."""
        token = create_access_token("alice")
        # Flip a character in the payload section
        parts = token.split(".")
        tampered_payload = parts[1][:-1] + ("A" if parts[1][-1] != "A" else "B")
        tampered_token = ".".join([parts[0], tampered_payload, parts[2]])
        with pytest.raises(TokenError):
            decode_access_token(tampered_token)

    def test_custom_expiry_is_respected(self) -> None:
        """Token with 1-hour expiry should decode successfully."""
        token = create_access_token("alice", expires_delta=timedelta(hours=1))
        payload = decode_access_token(token)
        assert payload["sub"] == "alice"

    def test_different_subjects_produce_different_tokens(self) -> None:
        token_alice = create_access_token("alice")
        token_bob = create_access_token("bob")
        assert token_alice != token_bob

    def test_token_signed_with_wrong_key_raises_token_error(self) -> None:
        """A token signed with key A must not decode with key B."""
        from jose import jwt
        claims = {"sub": "alice", "exp": 9999999999}
        token = jwt.encode(claims, "wrong-secret-key", algorithm="HS256")
        with pytest.raises(TokenError):
            decode_access_token(token)
