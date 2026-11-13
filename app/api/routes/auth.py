"""
Authentication routes.

POST /auth/register  — create a new account
POST /auth/login     — authenticate and receive a JWT
GET  /auth/me        — return the current authenticated user's profile

Security notes:
- Login returns the same error for wrong username AND wrong password.
  This prevents user enumeration attacks (an attacker can't tell which
  one was wrong).
- Registration checks for duplicate username/email to give useful errors,
  but login does not confirm whether the username exists.
- Passwords are never logged or included in responses.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import InMemoryStore, get_current_user, get_db
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import LoginRequest, TokenResponse, UserCreate, UserInDB, UserResponse

router = APIRouter(prefix="/auth", tags=["authentication"])
logger = get_logger(__name__)


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user account",
)
async def register(
    user_data: UserCreate,
    db: InMemoryStore = Depends(get_db),
) -> UserResponse:
    """
    Register a new user.

    Returns 409 if the username or email is already taken.
    Returns 201 with the created user (without password) on success.
    """
    # Check for conflicts before hashing the password (cheap check first)
    if db.username_exists(user_data.username):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already registered",
        )

    if db.email_exists(str(user_data.email)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    hashed = hash_password(user_data.password)
    user = db.create_user(
        username=user_data.username,
        email=str(user_data.email),
        hashed_password=hashed,
    )

    logger.info("user_registered", username=user.username, user_id=user.id)

    return UserResponse(id=user.id, username=user.username, email=user.email)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Authenticate and receive an access token",
)
async def login(
    credentials: LoginRequest,
    db: InMemoryStore = Depends(get_db),
) -> TokenResponse:
    """
    Authenticate with username and password.

    Returns a JWT access token on success.
    Returns 401 for any authentication failure (intentionally generic
    to prevent user enumeration).
    """
    # Generic error — same response whether username or password is wrong
    auth_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect username or password",
        headers={"WWW-Authenticate": "Bearer"},
    )

    user = db.get_user_by_username(credentials.username)

    if user is None:
        # Still call verify_password to prevent timing attacks
        # (attacker should not be able to tell from response time
        # whether the username existed)
        verify_password(credentials.password, "$2b$12$dummy_hash_for_timing_safety")
        logger.warning("login_failed_unknown_user", username=credentials.username)
        raise auth_error

    if not verify_password(credentials.password, user.hashed_password):
        logger.warning("login_failed_wrong_password", username=credentials.username)
        raise auth_error

    settings = get_settings()
    token = create_access_token(subject=user.username)

    logger.info("login_success", username=user.username, user_id=user.id)

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current user profile",
)
async def get_me(
    current_user: UserInDB = Depends(get_current_user),
) -> UserResponse:
    """
    Return the profile of the currently authenticated user.

    Requires a valid Bearer token. Returns 401 if not authenticated.
    This endpoint is useful as a "token check" — clients can call it
    to verify their token is still valid.
    """
    return UserResponse(
        id=current_user.id,
        username=current_user.username,
        email=current_user.email,
    )
