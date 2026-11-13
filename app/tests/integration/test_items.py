"""
Integration tests for items endpoints.

Covers: CRUD operations, pagination, authentication requirements,
and IDOR (Insecure Direct Object Reference) protection.
"""



class TestListItems:
    """Tests for GET /api/v1/items."""

    def test_list_items_is_public(self, client) -> None:
        """Read endpoints require no authentication."""
        response = client.get("/api/v1/items")
        assert response.status_code == 200

    def test_list_items_empty_initially(self, client) -> None:
        response = client.get("/api/v1/items")
        data = response.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_list_items_response_structure(self, client) -> None:
        response = client.get("/api/v1/items")
        data = response.json()
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "page_size" in data
        assert "pages" in data

    def test_list_items_pagination_defaults(self, client) -> None:
        response = client.get("/api/v1/items")
        data = response.json()
        assert data["page"] == 1
        assert data["page_size"] == 20

    def test_list_items_invalid_page_returns_422(self, client) -> None:
        response = client.get("/api/v1/items?page=0")
        assert response.status_code == 422

    def test_list_items_page_size_too_large_is_capped(
        self, client, logged_in_user
    ) -> None:
        """Page size > 100 should be rejected by query validation."""
        response = client.get("/api/v1/items?page_size=9999")
        assert response.status_code == 422

    def test_list_items_shows_created_items(self, client, logged_in_user) -> None:
        client.post(
            "/api/v1/items",
            json={"title": "Item one"},
            headers=logged_in_user["headers"],
        )
        response = client.get("/api/v1/items")
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["title"] == "Item one"


class TestGetItem:
    """Tests for GET /api/v1/items/{item_id}."""

    def test_get_item_is_public(self, client, logged_in_user) -> None:
        create = client.post(
            "/api/v1/items",
            json={"title": "Public item"},
            headers=logged_in_user["headers"],
        )
        item_id = create.json()["id"]

        response = client.get(f"/api/v1/items/{item_id}")
        assert response.status_code == 200

    def test_get_item_response_structure(self, client, logged_in_user) -> None:
        create = client.post(
            "/api/v1/items",
            json={"title": "Structured item", "priority": "high"},
            headers=logged_in_user["headers"],
        )
        item_id = create.json()["id"]

        response = client.get(f"/api/v1/items/{item_id}")
        data = response.json()
        assert data["id"] == item_id
        assert data["title"] == "Structured item"
        assert data["priority"] == "high"
        assert "created_at" in data
        assert "owner_id" in data

    def test_get_nonexistent_item_returns_404(self, client) -> None:
        response = client.get("/api/v1/items/99999")
        assert response.status_code == 404


class TestCreateItem:
    """Tests for POST /api/v1/items."""

    def test_create_item_requires_auth(self, client) -> None:
        response = client.post(
            "/api/v1/items",
            json={"title": "Needs auth"},
        )
        assert response.status_code == 401

    def test_create_item_returns_201(self, client, logged_in_user) -> None:
        response = client.post(
            "/api/v1/items",
            json={"title": "New item"},
            headers=logged_in_user["headers"],
        )
        assert response.status_code == 201

    def test_created_item_has_expected_fields(self, client, logged_in_user) -> None:
        response = client.post(
            "/api/v1/items",
            json={
                "title": "Detailed item",
                "description": "A longer description",
                "priority": "critical",
            },
            headers=logged_in_user["headers"],
        )
        data = response.json()
        assert data["title"] == "Detailed item"
        assert data["description"] == "A longer description"
        assert data["priority"] == "critical"
        assert data["status"] == "pending"
        assert "id" in data
        assert "created_at" in data

    def test_created_item_owner_is_authenticated_user(
        self, client, logged_in_user
    ) -> None:
        """owner_id must come from the token, not from client input."""
        create_response = client.post(
            "/api/v1/items",
            json={"title": "My item"},
            headers=logged_in_user["headers"],
        )
        # Get the authenticated user's ID
        me_response = client.get(
            "/api/v1/auth/me",
            headers=logged_in_user["headers"],
        )
        assert create_response.json()["owner_id"] == me_response.json()["id"]

    def test_create_item_empty_title_returns_422(self, client, logged_in_user) -> None:
        response = client.post(
            "/api/v1/items",
            json={"title": ""},
            headers=logged_in_user["headers"],
        )
        assert response.status_code == 422

    def test_create_item_invalid_priority_returns_422(
        self, client, logged_in_user
    ) -> None:
        response = client.post(
            "/api/v1/items",
            json={"title": "Bad priority", "priority": "urgent"},
            headers=logged_in_user["headers"],
        )
        assert response.status_code == 422


class TestUpdateItem:
    """Tests for PATCH /api/v1/items/{item_id}."""

    def test_update_item_requires_auth(self, client, logged_in_user) -> None:
        create = client.post(
            "/api/v1/items",
            json={"title": "To update"},
            headers=logged_in_user["headers"],
        )
        item_id = create.json()["id"]

        response = client.patch(
            f"/api/v1/items/{item_id}",
            json={"title": "Updated"},
        )
        assert response.status_code == 401

    def test_update_own_item_succeeds(self, client, logged_in_user) -> None:
        create = client.post(
            "/api/v1/items",
            json={"title": "Original title"},
            headers=logged_in_user["headers"],
        )
        item_id = create.json()["id"]

        response = client.patch(
            f"/api/v1/items/{item_id}",
            json={"title": "Updated title", "status": "in_progress"},
            headers=logged_in_user["headers"],
        )
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Updated title"
        assert data["status"] == "in_progress"

    def test_update_nonexistent_item_returns_404(self, client, logged_in_user) -> None:
        response = client.patch(
            "/api/v1/items/99999",
            json={"title": "Ghost"},
            headers=logged_in_user["headers"],
        )
        assert response.status_code == 404

    def test_idor_protection_on_update(self, client) -> None:
        """
        IDOR test: User A creates an item. User B should get 403 trying
        to update it, not 200 or 404.
        """
        # Register and log in User A
        client.post(
            "/api/v1/auth/register",
            json={"username": "usera", "email": "a@ex.com", "password": "usera123"},
        )
        login_a = client.post(
            "/api/v1/auth/login",
            json={"username": "usera", "password": "usera123"},
        )
        token_a = login_a.json()["access_token"]
        headers_a = {"Authorization": f"Bearer {token_a}"}

        # User A creates an item
        create = client.post(
            "/api/v1/items",
            json={"title": "User A's item"},
            headers=headers_a,
        )
        item_id = create.json()["id"]

        # Register and log in User B
        client.post(
            "/api/v1/auth/register",
            json={"username": "userb", "email": "b@ex.com", "password": "userb123"},
        )
        login_b = client.post(
            "/api/v1/auth/login",
            json={"username": "userb", "password": "userb123"},
        )
        token_b = login_b.json()["access_token"]
        headers_b = {"Authorization": f"Bearer {token_b}"}

        # User B attempts to update User A's item — must get 403
        response = client.patch(
            f"/api/v1/items/{item_id}",
            json={"title": "Hijacked by User B"},
            headers=headers_b,
        )
        assert response.status_code == 403


class TestDeleteItem:
    """Tests for DELETE /api/v1/items/{item_id}."""

    def test_delete_requires_auth(self, client, logged_in_user) -> None:
        create = client.post(
            "/api/v1/items",
            json={"title": "To delete"},
            headers=logged_in_user["headers"],
        )
        item_id = create.json()["id"]

        response = client.delete(f"/api/v1/items/{item_id}")
        assert response.status_code == 401

    def test_delete_own_item_returns_204(self, client, logged_in_user) -> None:
        create = client.post(
            "/api/v1/items",
            json={"title": "Will be deleted"},
            headers=logged_in_user["headers"],
        )
        item_id = create.json()["id"]

        response = client.delete(
            f"/api/v1/items/{item_id}",
            headers=logged_in_user["headers"],
        )
        assert response.status_code == 204

    def test_deleted_item_no_longer_accessible(self, client, logged_in_user) -> None:
        create = client.post(
            "/api/v1/items",
            json={"title": "Will be deleted"},
            headers=logged_in_user["headers"],
        )
        item_id = create.json()["id"]

        client.delete(
            f"/api/v1/items/{item_id}",
            headers=logged_in_user["headers"],
        )

        response = client.get(f"/api/v1/items/{item_id}")
        assert response.status_code == 404

    def test_idor_protection_on_delete(self, client) -> None:
        """User B should not be able to delete User A's item."""
        # User A
        client.post(
            "/api/v1/auth/register",
            json={"username": "owner", "email": "owner@ex.com", "password": "owner123"},
        )
        login_a = client.post(
            "/api/v1/auth/login",
            json={"username": "owner", "password": "owner123"},
        )
        headers_a = {"Authorization": f"Bearer {login_a.json()['access_token']}"}

        create = client.post(
            "/api/v1/items",
            json={"title": "Owner's item"},
            headers=headers_a,
        )
        item_id = create.json()["id"]

        # User B
        client.post(
            "/api/v1/auth/register",
            json={"username": "attacker", "email": "attacker@ex.com", "password": "attack123"},
        )
        login_b = client.post(
            "/api/v1/auth/login",
            json={"username": "attacker", "password": "attack123"},
        )
        headers_b = {"Authorization": f"Bearer {login_b.json()['access_token']}"}

        response = client.delete(f"/api/v1/items/{item_id}", headers=headers_b)
        assert response.status_code == 403

    def test_delete_nonexistent_returns_404(self, client, logged_in_user) -> None:
        response = client.delete(
            "/api/v1/items/99999",
            headers=logged_in_user["headers"],
        )
        assert response.status_code == 404
