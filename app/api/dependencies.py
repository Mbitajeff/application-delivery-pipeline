"""
FastAPI dependency injection.

Dependencies are injected into route handlers via FastAPI's Depends()
mechanism. This keeps route handlers thin, makes auth testable by
overriding dependencies in tests, and centralises security logic.

Pattern:
    - get_current_user: extracts and validates the JWT, returns the user
    - get_db: returns the in-memory store (swappable for a real DB)
    - require_item_owner: combines auth + ownership check
"""

from functools import lru_cache
from typing import TYPE_CHECKING

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.aws.events import ItemEventPublisher
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import TokenError, TokenExpiredError, decode_access_token
from app.models.user import UserInDB

if TYPE_CHECKING:
    from app.aws.dynamodb_store import DynamoDBStore

logger = get_logger(__name__)

# HTTPBearer extracts the token from the Authorization: Bearer <token> header.
# auto_error=False means we return 401 manually rather than getting FastAPI's
# default 403 — consistent error handling.
bearer_scheme = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# In-memory data store
# ---------------------------------------------------------------------------
# A real application would use a database. For this project the store is
# intentionally simple so the focus stays on the pipeline, not ORM setup.
# The structure mirrors what a database layer would expose.

class InMemoryStore:
    """Thread-local in-memory data store."""

    def __init__(self) -> None:
        self._users: dict[int, UserInDB] = {}
        self._items: dict[int, dict] = {}
        self._user_counter: int = 0
        self._item_counter: int = 0

        # Seed a default user for testing and DAST
        from app.core.security import hash_password
        self._user_counter += 1
        self._users[self._user_counter] = UserInDB(
            id=self._user_counter,
            username="admin",
            email="admin@example.com",
            hashed_password=hash_password("admin1234"),
        )

    # --- User operations ---

    def get_user_by_id(self, user_id: int) -> UserInDB | None:
        return self._users.get(user_id)

    def get_user_by_username(self, username: str) -> UserInDB | None:
        for user in self._users.values():
            if user.username == username:
                return user
        return None

    def create_user(self, username: str, email: str, hashed_password: str) -> UserInDB:
        self._user_counter += 1
        user = UserInDB(
            id=self._user_counter,
            username=username,
            email=email,
            hashed_password=hashed_password,
        )
        self._users[self._user_counter] = user
        return user

    def username_exists(self, username: str) -> bool:
        return any(u.username == username for u in self._users.values())

    def email_exists(self, email: str) -> bool:
        return any(u.email == email for u in self._users.values())

    # --- Item operations ---

    def get_item(self, item_id: int) -> dict | None:
        return self._items.get(item_id)

    def list_items(
        self,
        owner_id: int | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict], int]:
        items = list(self._items.values())
        if owner_id is not None:
            items = [i for i in items if i["owner_id"] == owner_id]
        total = len(items)
        start = (page - 1) * page_size
        end = start + page_size
        return items[start:end], total

    def create_item(self, data: dict) -> dict:
        self._item_counter += 1
        item = {**data, "id": self._item_counter}
        self._items[self._item_counter] = item
        return item

    def update_item(self, item_id: int, updates: dict) -> dict | None:
        item = self._items.get(item_id)
        if item is None:
            return None
        item.update({k: v for k, v in updates.items() if v is not None})
        return item

    def delete_item(self, item_id: int) -> bool:
        if item_id in self._items:
            del self._items[item_id]
            return True
        return False


# Single global store instance for the application lifecycle
_store = InMemoryStore()


@lru_cache
def _get_dynamodb_store(users_table: str, items_table: str) -> "DynamoDBStore":
    from app.aws.dynamodb_store import DynamoDBStore

    return DynamoDBStore(users_table=users_table, items_table=items_table)


def get_db() -> "InMemoryStore | DynamoDBStore":
    """
    Dependency: return the data store.

    STORAGE_BACKEND=memory (default) returns the in-process store;
    STORAGE_BACKEND=dynamodb returns the DynamoDB-backed store.

    In tests this is overridden via app.dependency_overrides to
    provide an isolated store per test.
    """
    settings = get_settings()
    if settings.storage_backend == "dynamodb":
        return _get_dynamodb_store(settings.dynamodb_users_table, settings.dynamodb_items_table)
    return _store


def get_event_publisher() -> ItemEventPublisher:
    """
    Dependency: return the item event publisher (S3 audit + SQS).

    With neither AUDIT_BUCKET nor EVENTS_QUEUE_URL set, publish() is a no-op.
    """
    settings = get_settings()
    return ItemEventPublisher(
        audit_bucket=settings.audit_bucket,
        queue_url=settings.events_queue_url,
    )


# ---------------------------------------------------------------------------
# Authentication dependencies
# ---------------------------------------------------------------------------

def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: InMemoryStore = Depends(get_db),
) -> UserInDB:
    """
    Dependency: extract and validate the JWT, return the authenticated user.

    Raises HTTP 401 for:
    - Missing Authorization header
    - Invalid or malformed token
    - Expired token
    - User not found (token subject doesn't match any user)
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if credentials is None:
        logger.debug("auth_missing_credentials")
        raise credentials_exception

    try:
        payload = decode_access_token(credentials.credentials)
    except TokenExpiredError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except TokenError:
        raise credentials_exception

    username: str | None = payload.get("sub")
    if username is None:
        logger.warning("auth_token_missing_subject")
        raise credentials_exception

    user = db.get_user_by_username(username)
    if user is None:
        logger.warning("auth_user_not_found", username=username)
        raise credentials_exception

    return user
