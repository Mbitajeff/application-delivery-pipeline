"""
Unit tests for Pydantic models.

Tests that input validation works correctly — these catch regressions
in validation logic before they affect the running application.
"""

import pytest
from pydantic import ValidationError

from app.models.item import ItemCreate, ItemStatus, ItemUpdate, Priority
from app.models.user import LoginRequest, UserCreate


class TestUserCreate:
    """Tests for user registration input validation."""

    def test_valid_user_creation(self) -> None:
        user = UserCreate(
            username="validuser",
            email="user@example.com",
            password="validpass1",
        )
        assert user.username == "validuser"
        assert user.email == "user@example.com"

    def test_username_too_short_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            UserCreate(username="ab", email="u@e.com", password="pass1word")
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("username",) for e in errors)

    def test_username_too_long_raises(self) -> None:
        with pytest.raises(ValidationError):
            UserCreate(
                username="a" * 51,
                email="u@e.com",
                password="pass1word",
            )

    def test_username_invalid_characters_raises(self) -> None:
        """Usernames must be alphanumeric with _ and - only."""
        with pytest.raises(ValidationError):
            UserCreate(
                username="user name!",
                email="u@e.com",
                password="pass1word",
            )

    def test_username_with_valid_special_chars(self) -> None:
        """Underscores and hyphens should be allowed."""
        user = UserCreate(
            username="user_name-123",
            email="u@e.com",
            password="pass1word",
        )
        assert user.username == "user_name-123"

    def test_invalid_email_raises(self) -> None:
        with pytest.raises(ValidationError):
            UserCreate(username="validuser", email="notanemail", password="pass1word")

    def test_password_too_short_raises(self) -> None:
        with pytest.raises(ValidationError):
            UserCreate(username="validuser", email="u@e.com", password="short1")

    def test_password_too_long_raises(self) -> None:
        with pytest.raises(ValidationError):
            UserCreate(
                username="validuser",
                email="u@e.com",
                password="a" * 129,
            )

    def test_password_without_digit_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            UserCreate(
                username="validuser",
                email="u@e.com",
                password="onlyletters",
            )
        errors = exc_info.value.errors()
        assert any("digit" in str(e["msg"]).lower() for e in errors)

    def test_password_without_letter_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            UserCreate(
                username="validuser",
                email="u@e.com",
                password="12345678",
            )
        errors = exc_info.value.errors()
        assert any("letter" in str(e["msg"]).lower() for e in errors)


class TestItemCreate:
    """Tests for item creation input validation."""

    def test_valid_item_creation(self) -> None:
        item = ItemCreate(title="Fix the bug", priority=Priority.HIGH)
        assert item.title == "Fix the bug"
        assert item.priority == Priority.HIGH
        assert item.description is None

    def test_default_priority_is_medium(self) -> None:
        item = ItemCreate(title="Some task")
        assert item.priority == Priority.MEDIUM

    def test_title_too_long_raises(self) -> None:
        with pytest.raises(ValidationError):
            ItemCreate(title="x" * 201)

    def test_empty_title_raises(self) -> None:
        with pytest.raises(ValidationError):
            ItemCreate(title="")

    def test_description_too_long_raises(self) -> None:
        with pytest.raises(ValidationError):
            ItemCreate(title="Valid title", description="x" * 2001)

    def test_invalid_priority_raises(self) -> None:
        with pytest.raises(ValidationError):
            ItemCreate(title="Task", priority="urgent")  # not in Priority enum

    def test_all_valid_priorities(self) -> None:
        for priority in Priority:
            item = ItemCreate(title="Task", priority=priority)
            assert item.priority == priority


class TestItemUpdate:
    """Tests for item update (PATCH) input validation."""

    def test_all_fields_optional(self) -> None:
        """ItemUpdate with no fields should be valid — PATCH semantics."""
        update = ItemUpdate()
        assert update.title is None
        assert update.description is None
        assert update.priority is None
        assert update.status is None

    def test_partial_update_valid(self) -> None:
        update = ItemUpdate(status=ItemStatus.COMPLETED)
        assert update.status == ItemStatus.COMPLETED
        assert update.title is None

    def test_invalid_status_raises(self) -> None:
        with pytest.raises(ValidationError):
            ItemUpdate(status="done")  # not in ItemStatus enum

    def test_title_still_validated_when_provided(self) -> None:
        with pytest.raises(ValidationError):
            ItemUpdate(title="")  # min_length=1 still applies


class TestLoginRequest:
    """Tests for login input validation."""

    def test_valid_login(self) -> None:
        req = LoginRequest(username="alice", password="secret123")
        assert req.username == "alice"

    def test_empty_username_raises(self) -> None:
        with pytest.raises(ValidationError):
            LoginRequest(username="", password="secret123")

    def test_empty_password_raises(self) -> None:
        with pytest.raises(ValidationError):
            LoginRequest(username="alice", password="")
