"""
Pytest configuration and shared fixtures.

Fixtures are scoped carefully:
- function scope: a fresh state per test (default, safest)
- module scope: shared across a test file (use for expensive setup)

The key pattern here is dependency override: we replace get_db with a
fresh InMemoryStore for each test, so tests are fully isolated.
"""

from datetime import UTC

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import InMemoryStore, get_db
from app.core.config import Settings, get_settings
from app.main import create_app


@pytest.fixture(autouse=True)
def clear_settings_cache() -> None:
    """
    Clear the lru_cache on get_settings before each test.

    This ensures that settings changes (via monkeypatch or env vars)
    take effect in each test rather than using the cached value.
    """
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def test_settings() -> Settings:
    """Return Settings configured for testing."""
    return Settings(
        environment="testing",
        secret_key="test-secret-key-32-bytes-minimum!",
        access_token_expire_minutes=5,
        debug=True,
    )


@pytest.fixture
def fresh_db() -> InMemoryStore:
    """
    Return a fresh InMemoryStore for each test.

    This store has the default seeded admin user but is otherwise empty.
    """
    return InMemoryStore()


@pytest.fixture
def client(test_settings: Settings, fresh_db: InMemoryStore) -> TestClient:
    """
    Return a FastAPI TestClient with isolated dependencies.

    Sets ENVIRONMENT and SECRET_KEY as env vars before constructing the app
    so that any code path calling get_settings() (including lifespan) picks
    up test values. Cleans up env vars and cache after the test.
    """
    import os

    # Set env vars so get_settings() returns test values everywhere —
    # including in lifespan and any code that calls get_settings() directly
    # (not via Depends). This is more reliable than module-level patching.
    env_overrides = {
        "ENVIRONMENT": test_settings.environment,
        "SECRET_KEY": test_settings.secret_key,
        "ACCESS_TOKEN_EXPIRE_MINUTES": str(test_settings.access_token_expire_minutes),
        "DEBUG": str(test_settings.debug).lower(),
    }
    original_env = {k: os.environ.get(k) for k in env_overrides}

    os.environ.update(env_overrides)
    get_settings.cache_clear()

    def override_db() -> InMemoryStore:
        return fresh_db

    application = create_app()
    application.dependency_overrides[get_db] = override_db

    try:
        with TestClient(application) as c:
            yield c
    finally:
        # Restore original environment
        for key, original_value in original_env.items():
            if original_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original_value
        get_settings.cache_clear()


@pytest.fixture
def auth_headers(test_settings: Settings) -> dict[str, str]:
    """
    Return Authorization headers for the seeded admin user.

    Creates a token signed with the test secret key directly via
    python-jose — no env var mutation, no settings side effects.
    """
    from datetime import datetime, timedelta

    from jose import jwt

    claims = {
        "sub": "admin",
        "iat": datetime.now(UTC),
        "exp": datetime.now(UTC) + timedelta(minutes=30),
    }
    token = jwt.encode(claims, test_settings.secret_key, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def registered_user(client: TestClient) -> dict:
    """Register a test user and return their credentials."""
    user_data = {
        "username": "testuser",
        "email": "testuser@example.com",
        "password": "testpass123",
    }
    response = client.post("/api/v1/auth/register", json=user_data)
    assert response.status_code == 201
    return user_data


@pytest.fixture
def logged_in_user(client: TestClient, registered_user: dict) -> dict:
    """Register a user and return their token + profile data."""
    response = client.post(
        "/api/v1/auth/login",
        json={
            "username": registered_user["username"],
            "password": registered_user["password"],
        },
    )
    assert response.status_code == 200
    token_data = response.json()
    return {
        **registered_user,
        "token": token_data["access_token"],
        "headers": {"Authorization": f"Bearer {token_data['access_token']}"},
    }
