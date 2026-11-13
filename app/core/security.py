"""
JWT authentication and password hashing utilities.

Design decisions:
- HS256 algorithm with configurable secret key
- Tokens carry minimal claims: sub (user id) + exp
- Passwords hashed with bcrypt via passlib
- Token creation and verification are pure functions for testability
- Exceptions are typed so calling code can handle them specifically
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
from jose import JWTError, jwt

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# passlib 1.7.4 is incompatible with bcrypt >= 4.0 (removed _bcrypt internal).
# We use bcrypt directly instead — same algorithm, no dependency on passlib.
# bcrypt is slow by design, which is exactly what we want for password hashing.
_BCRYPT_ROUNDS = 12  # OWASP recommended minimum as of 2024


class TokenError(Exception):
    """Raised when a JWT cannot be decoded or validated."""


class TokenExpiredError(TokenError):
    """Raised specifically when a JWT has expired."""


def hash_password(plain_password: str) -> str:
    """
    Hash a plain-text password using bcrypt.

    The resulting hash includes the salt, so each call produces a
    different output even for the same input. Never store plain passwords.
    """
    password_bytes = plain_password.encode("utf-8")
    salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    hashed = bcrypt.hashpw(password_bytes, salt)
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a plain-text password against a bcrypt hash.

    Returns True if they match, False otherwise.
    Does not raise — callers decide what to do with False.
    """
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8"),
        )
    except Exception:
        # Malformed hash (e.g. the dummy timing-safe hash) returns False
        return False


def create_access_token(
    subject: str,
    extra_claims: dict[str, Any] | None = None,
    expires_delta: timedelta | None = None,
) -> str:
    """
    Create a signed JWT access token.

    Args:
        subject: The token subject — typically a user ID or username.
        extra_claims: Optional additional claims to embed in the token.
        expires_delta: Override the default expiry from settings.

    Returns:
        A signed JWT string.
    """
    settings = get_settings()

    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.access_token_expire_minutes)

    now = datetime.now(UTC)
    expire = now + expires_delta

    claims: dict[str, Any] = {
        "sub": subject,
        "iat": now,
        "exp": expire,
    }

    if extra_claims:
        # Prevent overwriting reserved claims
        reserved = {"sub", "iat", "exp"}
        safe_extras = {k: v for k, v in extra_claims.items() if k not in reserved}
        claims.update(safe_extras)

    token = jwt.encode(claims, settings.secret_key, algorithm=settings.algorithm)

    logger.debug(
        "access_token_created",
        subject=subject,
        expires_at=expire.isoformat(),
    )

    return token


def decode_access_token(token: str) -> dict[str, Any]:
    """
    Decode and validate a JWT access token.

    Args:
        token: The JWT string to decode.

    Returns:
        The decoded claims dictionary.

    Raises:
        TokenExpiredError: If the token has expired.
        TokenError: If the token is malformed or the signature is invalid.
    """
    settings = get_settings()

    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.algorithm],
        )
        return payload

    except JWTError as exc:
        # Distinguish expired tokens from other errors — callers may want
        # to return 401 with a specific "token expired" message
        if "expired" in str(exc).lower():
            logger.debug("token_expired")
            raise TokenExpiredError("Access token has expired") from exc

        logger.warning("token_invalid", error=str(exc))
        raise TokenError(f"Could not validate token: {exc}") from exc
