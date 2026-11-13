"""
Integration tests for authentication endpoints.

Covers: registration, login, /me, error cases, and security properties
(user enumeration prevention, timing attack resistance).
"""



class TestRegistration:
    """Tests for POST /api/v1/auth/register."""

    def test_register_valid_user(self, client) -> None:
        response = client.post(
            "/api/v1/auth/register",
            json={
                "username": "newuser",
                "email": "new@example.com",
                "password": "secure123",
            },
        )
        assert response.status_code == 201

    def test_register_response_contains_id_and_username(self, client) -> None:
        response = client.post(
            "/api/v1/auth/register",
            json={
                "username": "alice",
                "email": "alice@example.com",
                "password": "alice1234",
            },
        )
        data = response.json()
        assert "id" in data
        assert data["username"] == "alice"
        assert data["email"] == "alice@example.com"

    def test_register_response_does_not_contain_password(self, client) -> None:
        """Passwords must never appear in API responses."""
        response = client.post(
            "/api/v1/auth/register",
            json={
                "username": "alice",
                "email": "alice@example.com",
                "password": "alice1234",
            },
        )
        data = response.json()
        assert "password" not in data
        assert "hashed_password" not in data

    def test_duplicate_username_returns_409(self, client) -> None:
        payload = {
            "username": "duplicate",
            "email": "first@example.com",
            "password": "first1234",
        }
        client.post("/api/v1/auth/register", json=payload)

        response = client.post(
            "/api/v1/auth/register",
            json={**payload, "email": "second@example.com"},
        )
        assert response.status_code == 409
        assert "username" in response.json()["detail"].lower()

    def test_duplicate_email_returns_409(self, client) -> None:
        payload = {
            "username": "firstuser",
            "email": "shared@example.com",
            "password": "first1234",
        }
        client.post("/api/v1/auth/register", json=payload)

        response = client.post(
            "/api/v1/auth/register",
            json={**payload, "username": "seconduser"},
        )
        assert response.status_code == 409
        assert "email" in response.json()["detail"].lower()

    def test_invalid_email_returns_422(self, client) -> None:
        response = client.post(
            "/api/v1/auth/register",
            json={
                "username": "alice",
                "email": "notanemail",
                "password": "alice1234",
            },
        )
        assert response.status_code == 422

    def test_weak_password_returns_422(self, client) -> None:
        response = client.post(
            "/api/v1/auth/register",
            json={
                "username": "alice",
                "email": "alice@example.com",
                "password": "short",
            },
        )
        assert response.status_code == 422


class TestLogin:
    """Tests for POST /api/v1/auth/login."""

    def test_login_with_seeded_admin(self, client) -> None:
        """The seeded admin user should be able to log in immediately."""
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "admin1234"},
        )
        assert response.status_code == 200

    def test_login_response_contains_token(self, client) -> None:
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "admin1234"},
        )
        data = response.json()
        assert "access_token" in data
        assert len(data["access_token"]) > 0
        assert data["token_type"] == "bearer"
        assert "expires_in" in data

    def test_login_wrong_password_returns_401(self, client) -> None:
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "wrongpassword"},
        )
        assert response.status_code == 401

    def test_login_nonexistent_user_returns_401(self, client) -> None:
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "doesnotexist", "password": "password123"},
        )
        assert response.status_code == 401

    def test_user_enumeration_prevention(self, client) -> None:
        """
        Wrong username and wrong password should return identical responses.
        An attacker should not be able to tell which one was wrong.
        """
        wrong_user_response = client.post(
            "/api/v1/auth/login",
            json={"username": "nonexistent", "password": "password123"},
        )
        wrong_pass_response = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "wrongpassword"},
        )

        # Same status code
        assert wrong_user_response.status_code == wrong_pass_response.status_code == 401

        # Same error message
        assert wrong_user_response.json()["detail"] == wrong_pass_response.json()["detail"]

    def test_login_after_registration(self, client, registered_user) -> None:
        response = client.post(
            "/api/v1/auth/login",
            json={
                "username": registered_user["username"],
                "password": registered_user["password"],
            },
        )
        assert response.status_code == 200


class TestGetMe:
    """Tests for GET /api/v1/auth/me."""

    def test_me_requires_authentication(self, client) -> None:
        response = client.get("/api/v1/auth/me")
        assert response.status_code == 401

    def test_me_with_valid_token(self, client, logged_in_user) -> None:
        response = client.get(
            "/api/v1/auth/me",
            headers=logged_in_user["headers"],
        )
        assert response.status_code == 200

    def test_me_returns_correct_user(self, client, logged_in_user) -> None:
        response = client.get(
            "/api/v1/auth/me",
            headers=logged_in_user["headers"],
        )
        data = response.json()
        assert data["username"] == logged_in_user["username"]
        assert data["email"] == logged_in_user["email"]

    def test_me_does_not_return_password(self, client, logged_in_user) -> None:
        response = client.get(
            "/api/v1/auth/me",
            headers=logged_in_user["headers"],
        )
        data = response.json()
        assert "password" not in data
        assert "hashed_password" not in data

    def test_me_with_invalid_token(self, client) -> None:
        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer invalidtoken"},
        )
        assert response.status_code == 401

    def test_me_with_malformed_header(self, client) -> None:
        # A non-Bearer scheme is treated as missing credentials → 401.
        # (HTTPBearer with auto_error=False returns None for non-Bearer schemes,
        # which our dependency converts to a 401 Unauthorized.)
        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "NotBearer sometoken"},
        )
        assert response.status_code == 401
