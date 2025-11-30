"""Unit tests for the UserStore and User model."""

import tempfile
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from remotellm.broker.users import User, UserRole, UserStore


class TestUser:
    """Tests for the User dataclass."""

    def test_user_creation_defaults(self):
        """Test creating a user with default values."""
        user = User(
            gitlab_username="testuser",
            gitlab_id=12345,
            api_key="sk-test123",
        )

        assert user.gitlab_username == "testuser"
        assert user.gitlab_id == 12345
        assert user.api_key == "sk-test123"
        assert user.role == UserRole.USER
        assert user.blocked is False
        assert user.last_used is None
        assert isinstance(user.created_at, datetime)

    def test_user_to_dict(self):
        """Test serializing user to dictionary."""
        user = User(
            gitlab_username="admin",
            gitlab_id=99999,
            api_key="sk-admin-key",
            role=UserRole.ADMIN,
            blocked=True,
        )

        data = user.to_dict()

        assert data["gitlab_username"] == "admin"
        assert data["gitlab_id"] == 99999
        assert data["api_key"] == "sk-admin-key"
        assert data["role"] == "admin"
        assert data["blocked"] is True
        assert data["last_used"] is None
        assert "created_at" in data

    def test_user_from_dict(self):
        """Test deserializing user from dictionary."""
        data = {
            "gitlab_username": "fromdict",
            "gitlab_id": 55555,
            "api_key": "sk-fromdict",
            "role": "admin",
            "created_at": "2024-01-15T10:30:00",
            "last_used": "2024-01-15T14:00:00",
            "blocked": False,
        }

        user = User.from_dict(data)

        assert user.gitlab_username == "fromdict"
        assert user.gitlab_id == 55555
        assert user.api_key == "sk-fromdict"
        assert user.role == UserRole.ADMIN
        assert user.blocked is False
        assert user.last_used is not None

    def test_user_from_dict_defaults(self):
        """Test deserializing user with missing optional fields."""
        data = {
            "gitlab_username": "minimal",
            "gitlab_id": 11111,
            "api_key": "sk-minimal",
            "created_at": "2024-01-01T00:00:00",
        }

        user = User.from_dict(data)

        assert user.role == UserRole.USER  # Default
        assert user.blocked is False  # Default
        assert user.last_used is None


class TestUserStore:
    """Tests for the UserStore class."""

    def test_create_first_user_becomes_admin(self):
        """Test that the first user created becomes admin."""
        store = UserStore(file_path=None)  # In-memory mode

        user = store.create_user("firstuser", 12345)

        assert user.role == UserRole.ADMIN
        assert user.api_key.startswith("sk-")
        assert len(user.api_key) == 35  # "sk-" + 32 hex chars

    def test_create_subsequent_users_are_regular(self):
        """Test that subsequent users get USER role."""
        store = UserStore(file_path=None)

        first = store.create_user("first", 1)
        second = store.create_user("second", 2)
        third = store.create_user("third", 3)

        assert first.role == UserRole.ADMIN
        assert second.role == UserRole.USER
        assert third.role == UserRole.USER

    def test_get_by_username(self):
        """Test retrieving user by username."""
        store = UserStore(file_path=None)
        store.create_user("findme", 12345)

        found = store.get_by_username("findme")
        not_found = store.get_by_username("doesnotexist")

        assert found is not None
        assert found.gitlab_username == "findme"
        assert not_found is None

    def test_get_by_api_key(self):
        """Test retrieving user by API key."""
        store = UserStore(file_path=None)
        user = store.create_user("apiuser", 12345)

        found = store.get_by_api_key(user.api_key)
        not_found = store.get_by_api_key("sk-invalid")

        assert found is not None
        assert found.gitlab_username == "apiuser"
        assert not_found is None

    def test_get_all_users(self):
        """Test retrieving all users."""
        store = UserStore(file_path=None)
        store.create_user("user1", 1)
        store.create_user("user2", 2)
        store.create_user("user3", 3)

        all_users = store.get_all()

        assert len(all_users) == 3
        usernames = {u.gitlab_username for u in all_users}
        assert usernames == {"user1", "user2", "user3"}

    def test_set_blocked(self):
        """Test blocking and unblocking users."""
        store = UserStore(file_path=None)
        store.create_user("blockme", 12345)

        # Block user
        result = store.set_blocked("blockme", True)
        user = store.get_by_username("blockme")

        assert result is True
        assert user.blocked is True

        # Unblock user
        store.set_blocked("blockme", False)
        user = store.get_by_username("blockme")

        assert user.blocked is False

    def test_set_blocked_nonexistent_user(self):
        """Test blocking a user that doesn't exist."""
        store = UserStore(file_path=None)

        result = store.set_blocked("ghost", True)

        assert result is False

    def test_set_role(self):
        """Test changing user role."""
        store = UserStore(file_path=None)
        store.create_user("admin1", 1)
        store.create_user("promoteuser", 2)

        # Promote to admin
        result = store.set_role("promoteuser", UserRole.ADMIN)
        user = store.get_by_username("promoteuser")

        assert result is True
        assert user.role == UserRole.ADMIN

        # Demote back to user
        store.set_role("promoteuser", UserRole.USER)
        user = store.get_by_username("promoteuser")

        assert user.role == UserRole.USER

    def test_set_role_nonexistent_user(self):
        """Test changing role for nonexistent user."""
        store = UserStore(file_path=None)

        result = store.set_role("ghost", UserRole.ADMIN)

        assert result is False

    def test_validate_api_key(self):
        """Test API key validation."""
        store = UserStore(file_path=None)
        user = store.create_user("validuser", 12345)

        # Valid key
        valid = store.validate_api_key(user.api_key)
        assert valid is not None
        assert valid.gitlab_username == "validuser"

        # Invalid key
        invalid = store.validate_api_key("sk-invalid")
        assert invalid is None

    def test_validate_api_key_blocked_user(self):
        """Test that blocked users fail validation."""
        store = UserStore(file_path=None)
        user = store.create_user("blockeduser", 12345)
        store.set_blocked("blockeduser", True)

        result = store.validate_api_key(user.api_key)

        assert result is None

    def test_regenerate_api_key(self):
        """Test regenerating API key."""
        store = UserStore(file_path=None)
        user = store.create_user("regenuser", 12345)
        old_key = user.api_key

        new_key = store.regenerate_api_key("regenuser")

        assert new_key is not None
        assert new_key != old_key
        assert new_key.startswith("sk-")

        # Old key should not work
        assert store.get_by_api_key(old_key) is None

        # New key should work
        assert store.get_by_api_key(new_key) is not None

    def test_regenerate_api_key_nonexistent_user(self):
        """Test regenerating API key for nonexistent user."""
        store = UserStore(file_path=None)

        result = store.regenerate_api_key("ghost")

        assert result is None

    def test_delete_user(self):
        """Test deleting a user."""
        store = UserStore(file_path=None)
        user = store.create_user("deleteme", 12345)
        api_key = user.api_key

        result = store.delete_user("deleteme")

        assert result is True
        assert store.get_by_username("deleteme") is None
        assert store.get_by_api_key(api_key) is None

    def test_delete_nonexistent_user(self):
        """Test deleting a nonexistent user."""
        store = UserStore(file_path=None)

        result = store.delete_user("ghost")

        assert result is False

    def test_update_last_used(self):
        """Test updating last_used timestamp."""
        store = UserStore(file_path=None)
        user = store.create_user("activeuser", 12345)
        assert user.last_used is None

        store.update_last_used(user)

        assert user.last_used is not None
        assert isinstance(user.last_used, datetime)

    def test_persistence_save_and_load(self):
        """Test that users are persisted to YAML file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "users.yaml"

            # Create store and add users
            store1 = UserStore(file_path)
            store1.create_user("persist1", 1)
            store1.create_user("persist2", 2)
            store1.set_role("persist2", UserRole.ADMIN)
            store1.set_blocked("persist1", True)

            # Create new store from same file
            store2 = UserStore(file_path)

            assert len(store2.get_all()) == 2

            user1 = store2.get_by_username("persist1")
            user2 = store2.get_by_username("persist2")

            assert user1 is not None
            assert user1.blocked is True
            assert user1.role == UserRole.ADMIN  # First user is admin

            assert user2 is not None
            assert user2.role == UserRole.ADMIN  # Promoted to admin

    def test_persistence_yaml_format(self):
        """Test the YAML file format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "users.yaml"

            store = UserStore(file_path)
            store.create_user("yamluser", 12345)

            # Read the YAML file directly
            with open(file_path) as f:
                data = yaml.safe_load(f)

            assert "users" in data
            assert len(data["users"]) == 1
            assert data["users"][0]["gitlab_username"] == "yamluser"
            assert data["users"][0]["gitlab_id"] == 12345
            assert data["users"][0]["role"] == "admin"
            assert "api_key" in data["users"][0]

    def test_load_nonexistent_file(self):
        """Test loading from a file that doesn't exist."""
        store = UserStore(Path("/nonexistent/path/users.yaml"))

        # Should start with empty store, no error
        assert len(store.get_all()) == 0

    def test_create_user_with_explicit_role(self):
        """Test creating a user with an explicit role."""
        store = UserStore(file_path=None)

        # Create first user as regular user (override default admin)
        user = store.create_user("explicit", 12345, role=UserRole.USER)

        assert user.role == UserRole.USER
