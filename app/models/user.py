"""
User data models.

Separate models for different concerns:
- UserBase: shared fields
- UserCreate: input validation for registration
- UserResponse: what the API returns (never includes password)
- UserInDB: internal representation with hashed password
- TokenResponse: authentication response

This pattern prevents accidental password exposure in API responses.
"""

from pydantic import BaseModel, EmailStr, Field, field_validator


class UserBase(BaseModel):
    """Fields shared across all user representations."""

    username: str = Field(
        min_length=3,
        max_length=50,
        pattern=r"^[a-zA-Z0-9_-]+$",
        description="Alphanumeric username with underscores and hyphens allowed",
        examples=["alice", "bob_smith", "user-123"],
    )
    email: EmailStr = Field(description="Valid email address")


class UserCreate(UserBase):
    """Input model for user registration. Includes password validation."""

    password: str = Field(
        min_length=8,
        max_length=128,
        description="Password (min 8 characters)",
    )

    @field_validator("password")
    @classmethod
    def password_complexity(cls, v: str) -> str:
        """
        Basic password complexity check.

        Requires at least one digit and one letter.
        Not exhaustive — a production system would use zxcvbn or similar.
        """
        has_letter = any(c.isalpha() for c in v)
        has_digit = any(c.isdigit() for c in v)

        if not has_letter:
            raise ValueError("Password must contain at least one letter")
        if not has_digit:
            raise ValueError("Password must contain at least one digit")

        return v


class UserResponse(UserBase):
    """Public-facing user representation. Never includes password hash."""

    id: int = Field(description="Unique user identifier", examples=[1, 42])

    model_config = {"from_attributes": True}


class UserInDB(UserBase):
    """
    Internal user representation including the password hash.

    This model must NEVER be returned from an API endpoint.
    It exists solely for internal authentication logic.
    """

    id: int
    hashed_password: str


class LoginRequest(BaseModel):
    """Credentials for the login endpoint."""

    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    """Response returned after successful authentication."""

    access_token: str = Field(description="JWT access token")
    token_type: str = Field(default="bearer", description="Token type (always 'bearer')")
    expires_in: int = Field(description="Token lifetime in seconds")
