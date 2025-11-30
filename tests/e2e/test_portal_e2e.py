"""End-to-end tests for the web portal with broker and connectors."""

import tempfile
from pathlib import Path

import pytest
import yaml

from remotellm.broker.admin import RequestLogger
from remotellm.broker.router import ModelRouter
from remotellm.broker.users import UserRole, UserStore


class TestPortalE2E:
    """End-to-end tests for the web portal functionality."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for test files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def user_store(self, temp_dir):
        """Create a UserStore with temporary file."""
        return UserStore(temp_dir / "users.yaml")

    @pytest.fixture
    def router(self):
        """Create a ModelRouter."""
        return ModelRouter()

    @pytest.fixture
    def request_logger(self):
        """Create a RequestLogger."""
        return RequestLogger(max_logs=100)

    def test_first_user_admin_flow(self, user_store):
        """Test that first user becomes admin, subsequent users are regular."""
        # First user (admin)
        admin = user_store.create_user("first_admin", 1001)
        assert admin.role == UserRole.ADMIN
        assert admin.api_key.startswith("sk-")

        # Second user (regular)
        user = user_store.create_user("regular_user", 1002)
        assert user.role == UserRole.USER

        # Admin can promote user
        user_store.set_role("regular_user", UserRole.ADMIN)
        promoted = user_store.get_by_username("regular_user")
        assert promoted.role == UserRole.ADMIN

    def test_user_blocking_flow(self, user_store):
        """Test blocking and unblocking users."""
        user_store.create_user("admin", 1)
        user_store.create_user("target_user", 2)

        # Block user
        user_store.set_blocked("target_user", True)

        # Blocked user cannot validate API key
        target = user_store.get_by_username("target_user")
        assert user_store.validate_api_key(target.api_key) is None

        # Unblock user
        user_store.set_blocked("target_user", False)
        assert user_store.validate_api_key(target.api_key) is not None

    def test_api_key_regeneration_flow(self, user_store):
        """Test regenerating API keys."""
        user = user_store.create_user("key_user", 1001)
        original_key = user.api_key

        # Regenerate key
        new_key = user_store.regenerate_api_key("key_user")

        # Old key no longer works
        assert user_store.validate_api_key(original_key) is None

        # New key works
        assert user_store.validate_api_key(new_key) is not None

        # User object updated
        user = user_store.get_by_username("key_user")
        assert user.api_key == new_key

    def test_connector_registration_visibility(self, router):
        """Test that registered connectors are visible in portal."""
        # No connectors initially
        assert len(router.get_connector_info()) == 0
        assert len(router.available_models) == 0

        # Register connector 1
        router.on_connector_registered(
            connector_id="connector-1",
            models=["gpt-4", "gpt-3.5-turbo"],
            llm_api_key=None,
        )

        info = router.get_connector_info()
        assert len(info) == 1
        assert info[0]["id"] == "connector-1"
        assert len(info[0]["models"]) == 2

        # Register connector 2
        router.on_connector_registered(
            connector_id="connector-2",
            models=["claude-3", "claude-2"],
            llm_api_key=None,
        )

        info = router.get_connector_info()
        assert len(info) == 2
        assert len(router.available_models) == 4

        # Disconnect connector 1
        router.on_connector_disconnected("connector-1")

        info = router.get_connector_info()
        assert len(info) == 1
        assert info[0]["id"] == "connector-2"
        assert len(router.available_models) == 2

    def test_request_logging_flow(self, request_logger):
        """Test request logging for admin dashboard."""
        # Log some requests
        request_logger.log_request("req-1", "alice", "gpt-4", "success", 150)
        request_logger.log_request("req-2", "bob", "claude-3", "success", 200)
        request_logger.log_request("req-3", "alice", "gpt-4", "error", 50)
        request_logger.log_request("req-4", None, "gpt-4", "success", 100)

        # Admin can see all logs
        all_logs = request_logger.get_logs()
        assert len(all_logs) == 4

        # Admin can filter by user
        alice_logs = request_logger.get_logs(user="alice")
        assert len(alice_logs) == 2

        # Admin can filter by model
        gpt4_logs = request_logger.get_logs(model="gpt-4")
        assert len(gpt4_logs) == 3

        # Admin can filter by status
        error_logs = request_logger.get_logs(status="error")
        assert len(error_logs) == 1

    def test_user_persistence_across_restarts(self, temp_dir):
        """Test that users persist across UserStore restarts."""
        users_file = temp_dir / "users.yaml"

        # Phase 1: Create and modify users
        store1 = UserStore(users_file)
        store1.create_user("persistent_admin", 1001)
        store1.create_user("persistent_user", 1002)
        store1.set_role("persistent_user", UserRole.ADMIN)
        store1.set_blocked("persistent_admin", True)

        # Phase 2: Simulate restart
        store2 = UserStore(users_file)

        # Verify persistence
        admin = store2.get_by_username("persistent_admin")
        user = store2.get_by_username("persistent_user")

        assert admin is not None
        assert admin.blocked is True

        assert user is not None
        assert user.role == UserRole.ADMIN

    def test_full_admin_workflow(self, temp_dir):
        """Test complete admin workflow."""
        users_file = temp_dir / "users.yaml"
        store = UserStore(users_file)

        # Admin creates account (first user)
        admin = store.create_user("super_admin", 1)
        assert admin.role == UserRole.ADMIN

        # Users register
        user1 = store.create_user("employee1", 2)
        user2 = store.create_user("employee2", 3)
        user3 = store.create_user("contractor", 4)

        # Admin views all users
        all_users = store.get_all()
        assert len(all_users) == 4

        # Admin blocks contractor
        store.set_blocked("contractor", True)

        # Contractor cannot use API
        contractor = store.get_by_username("contractor")
        assert store.validate_api_key(contractor.api_key) is None

        # Admin promotes employee1
        store.set_role("employee1", UserRole.ADMIN)

        # Admin deletes employee2
        store.delete_user("employee2")
        assert store.get_by_username("employee2") is None

        # Verify final state
        all_users = store.get_all()
        assert len(all_users) == 3

        admins = [u for u in all_users if u.role == UserRole.ADMIN]
        assert len(admins) == 2

    def test_multi_connector_model_routing(self, router):
        """Test model routing across multiple connectors."""
        # Register connectors with different models
        router.on_connector_registered("conn-openai", ["gpt-4", "gpt-3.5"], None)
        router.on_connector_registered("conn-anthropic", ["claude-3", "claude-2"], None)
        router.on_connector_registered("conn-local", ["llama-3", "mistral"], None)

        # Verify all models available
        models = router.available_models
        assert len(models) == 6
        assert "gpt-4" in models
        assert "claude-3" in models
        assert "llama-3" in models

        # Verify routing returns correct connector
        gpt4_route = router.get_route("gpt-4")
        assert gpt4_route is not None
        assert gpt4_route[0] == "conn-openai"  # (connector_id, llm_api_key)

        claude_route = router.get_route("claude-3")
        assert claude_route is not None
        assert claude_route[0] == "conn-anthropic"

        # Unknown model returns None
        unknown_route = router.get_route("unknown-model")
        assert unknown_route is None

    def test_connector_config_yaml_format(self, temp_dir):
        """Test connector config YAML file format."""
        config_file = temp_dir / "connectors.yaml"

        config_data = {
            "connectors": [
                {"token": "token-1", "llm_api_key": "sk-key-1"},
                {"token": "token-2", "llm_api_key": "sk-key-2"},
                {"token": "token-3"},  # No API key
            ]
        }

        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        # Verify file format
        with open(config_file) as f:
            loaded = yaml.safe_load(f)

        assert "connectors" in loaded
        assert len(loaded["connectors"]) == 3
        assert loaded["connectors"][0]["token"] == "token-1"
        assert loaded["connectors"][2].get("llm_api_key") is None

    def test_user_yaml_format(self, temp_dir):
        """Test user YAML file format includes all fields."""
        users_file = temp_dir / "users.yaml"
        store = UserStore(users_file)

        # Create user
        store.create_user("format_test_user", 12345)

        # Read raw YAML
        with open(users_file) as f:
            data = yaml.safe_load(f)

        user_data = data["users"][0]

        # Verify all required fields present
        assert "gitlab_username" in user_data
        assert "gitlab_id" in user_data
        assert "api_key" in user_data
        assert "role" in user_data
        assert "created_at" in user_data
        assert "blocked" in user_data

        # Verify values
        assert user_data["gitlab_username"] == "format_test_user"
        assert user_data["gitlab_id"] == 12345
        assert user_data["role"] == "admin"  # First user
        assert user_data["blocked"] is False


class TestPortalSecurityE2E:
    """Security-focused E2E tests."""

    @pytest.fixture
    def user_store(self):
        """Create an in-memory UserStore."""
        return UserStore(file_path=None)

    def test_blocked_user_cannot_access_api(self, user_store):
        """Test that blocked users cannot validate their API key."""
        user = user_store.create_user("blocked_test", 1001)
        api_key = user.api_key

        # Initially valid
        assert user_store.validate_api_key(api_key) is not None

        # Block user
        user_store.set_blocked("blocked_test", True)

        # No longer valid
        assert user_store.validate_api_key(api_key) is None

    def test_regenerated_key_invalidates_old(self, user_store):
        """Test that regenerating API key invalidates the old one."""
        user = user_store.create_user("regen_test", 1001)
        old_key = user.api_key

        # Regenerate
        new_key = user_store.regenerate_api_key("regen_test")

        # Old key invalid
        assert user_store.validate_api_key(old_key) is None

        # New key valid
        assert user_store.validate_api_key(new_key) is not None

    def test_deleted_user_key_invalid(self, user_store):
        """Test that deleted user's API key becomes invalid."""
        user = user_store.create_user("delete_test", 1001)
        api_key = user.api_key

        # Initially valid
        assert user_store.validate_api_key(api_key) is not None

        # Delete user
        user_store.delete_user("delete_test")

        # Key no longer valid
        assert user_store.validate_api_key(api_key) is None

    def test_api_key_format(self, user_store):
        """Test that API keys have correct format."""
        user = user_store.create_user("key_format_test", 1001)

        # Check format: sk- prefix + 32 hex chars
        assert user.api_key.startswith("sk-")
        assert len(user.api_key) == 35  # 3 + 32

        # Verify it's valid hex
        hex_part = user.api_key[3:]
        int(hex_part, 16)  # Should not raise

    def test_unique_api_keys(self, user_store):
        """Test that each user gets a unique API key."""
        keys = set()

        for i in range(100):
            user = user_store.create_user(f"unique_test_{i}", 1000 + i)
            assert user.api_key not in keys
            keys.add(user.api_key)

        assert len(keys) == 100
