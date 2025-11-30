"""Integration tests for the web portal (auth and admin handlers)."""

import base64
import hashlib
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp_jinja2
import jinja2
import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase
from aiohttp_session import setup as setup_session
from aiohttp_session.cookie_storage import EncryptedCookieStorage

from remotellm.broker.admin import AdminHandler, RequestLogger
from remotellm.broker.auth import AuthHandler
from remotellm.broker.config import BrokerConfig
from remotellm.broker.router import ModelRouter
from remotellm.broker.users import User, UserRole, UserStore


def create_test_config() -> BrokerConfig:
    """Create a test broker configuration with OAuth enabled."""
    return BrokerConfig(
        host="localhost",
        port=8443,
        connector_tokens=["test-token"],
        user_api_keys=["sk-test"],
        gitlab_url="https://gitlab.example.com",
        gitlab_client_id="test-client-id",
        gitlab_client_secret="test-client-secret",
        gitlab_redirect_uri="http://localhost:8443/auth/callback",
        public_url="http://localhost:8443",
        session_secret="test-session-secret-32-chars-long",
    )


def setup_test_app(
    user_store: UserStore,
    router: ModelRouter,
    config: BrokerConfig,
    request_logger: RequestLogger | None = None,
) -> web.Application:
    """Set up a test aiohttp application with auth and admin handlers."""
    app = web.Application()

    # Set up Jinja2 templates
    template_dir = Path(__file__).parent.parent.parent / "src" / "remotellm" / "broker" / "templates"
    aiohttp_jinja2.setup(
        app,
        loader=jinja2.FileSystemLoader(str(template_dir)),
    )

    # Set up session storage with a valid Fernet object
    from cryptography.fernet import Fernet
    fernet = Fernet(Fernet.generate_key())
    setup_session(app, EncryptedCookieStorage(fernet))

    # Set up auth handler
    auth_handler = AuthHandler(config, user_store, router)
    auth_handler.setup_routes(app)

    # Set up admin handler
    if request_logger is None:
        request_logger = RequestLogger()
    admin_handler = AdminHandler(user_store, router, request_logger)
    admin_handler.setup_routes(app)

    return app


class TestAuthRoutes(AioHTTPTestCase):
    """Tests for auth routes."""

    async def get_application(self):
        """Create application for testing."""
        self.user_store = UserStore(file_path=None)
        self.router = ModelRouter()
        self.config = create_test_config()
        return setup_test_app(self.user_store, self.router, self.config)

    async def test_home_redirects_to_login(self):
        """Test that home page redirects to login when not authenticated."""
        resp = await self.client.request("GET", "/", allow_redirects=False)
        assert resp.status == 302
        assert resp.headers["Location"] == "/auth/login"

    async def test_login_page_renders(self):
        """Test that login page renders correctly."""
        resp = await self.client.request("GET", "/auth/login")
        assert resp.status == 200
        text = await resp.text()
        assert "Login with GitLab" in text or "GitLab" in text

    async def test_dashboard_requires_auth(self):
        """Test that dashboard redirects to login when not authenticated."""
        resp = await self.client.request("GET", "/dashboard", allow_redirects=False)
        assert resp.status == 302
        assert "/auth/login" in resp.headers["Location"]

    async def test_services_requires_auth(self):
        """Test that services page redirects to login when not authenticated."""
        resp = await self.client.request("GET", "/services", allow_redirects=False)
        assert resp.status == 302
        assert "/auth/login" in resp.headers["Location"]


class TestAdminRoutes(AioHTTPTestCase):
    """Tests for admin routes."""

    async def get_application(self):
        """Create application for testing."""
        self.user_store = UserStore(file_path=None)
        self.router = ModelRouter()
        self.config = create_test_config()
        self.request_logger = RequestLogger()
        return setup_test_app(self.user_store, self.router, self.config, self.request_logger)

    async def test_admin_requires_auth(self):
        """Test that admin dashboard requires authentication."""
        resp = await self.client.request("GET", "/admin", allow_redirects=False)
        assert resp.status == 302
        assert "/auth/login" in resp.headers["Location"]

    async def test_admin_users_requires_auth(self):
        """Test that admin users page requires authentication."""
        resp = await self.client.request("GET", "/admin/users", allow_redirects=False)
        assert resp.status == 302

    async def test_admin_logs_requires_auth(self):
        """Test that admin logs page requires authentication."""
        resp = await self.client.request("GET", "/admin/logs", allow_redirects=False)
        assert resp.status == 302


class TestUserStoreIntegration:
    """Integration tests for UserStore with file persistence."""

    def test_full_user_lifecycle(self):
        """Test complete user lifecycle with persistence."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "users.yaml"

            # Phase 1: Create users
            store1 = UserStore(file_path)
            admin = store1.create_user("admin_user", 1)
            user1 = store1.create_user("regular_user", 2)
            user2 = store1.create_user("another_user", 3)

            assert admin.role == UserRole.ADMIN
            assert user1.role == UserRole.USER
            assert user2.role == UserRole.USER

            # Phase 2: Modify users
            store1.set_blocked("another_user", True)
            store1.set_role("regular_user", UserRole.ADMIN)

            # Phase 3: Load in new store and verify persistence
            store2 = UserStore(file_path)

            loaded_admin = store2.get_by_username("admin_user")
            loaded_user1 = store2.get_by_username("regular_user")
            loaded_user2 = store2.get_by_username("another_user")

            assert loaded_admin.role == UserRole.ADMIN
            assert loaded_user1.role == UserRole.ADMIN
            assert loaded_user2.blocked is True

            # Phase 4: Regenerate API key and verify
            old_key = loaded_user1.api_key
            new_key = store2.regenerate_api_key("regular_user")

            assert new_key != old_key
            assert store2.get_by_api_key(old_key) is None
            assert store2.get_by_api_key(new_key) is not None

            # Phase 5: Delete user
            store2.delete_user("another_user")

            store3 = UserStore(file_path)
            assert store3.get_by_username("another_user") is None
            assert len(store3.get_all()) == 2


class TestRequestLoggerIntegration:
    """Integration tests for RequestLogger."""

    def test_high_volume_logging(self):
        """Test logging many requests."""
        logger = RequestLogger(max_logs=50)

        # Log 100 requests
        for i in range(100):
            logger.log_request(
                correlation_id=f"req-{i:04d}",
                user=f"user{i % 5}",
                model=f"model{i % 3}",
                status="success" if i % 4 != 0 else "error",
                duration_ms=100 + i,
            )

        # Should only have 50 logs
        all_logs = logger.get_logs()
        assert len(all_logs) == 50

        # Should have the most recent 50
        assert all_logs[0].correlation_id == "req-0099"
        assert all_logs[-1].correlation_id == "req-0050"

    def test_complex_filtering(self):
        """Test complex filtering scenarios."""
        logger = RequestLogger()

        # Create diverse logs
        users = ["alice", "bob", "charlie", None]
        models = ["gpt-4", "claude-3", "llama-3"]
        statuses = ["success", "error", "timeout"]

        for i in range(24):
            logger.log_request(
                correlation_id=f"complex-{i:03d}",
                user=users[i % 4],
                model=models[i % 3],
                status=statuses[i % 3],
                duration_ms=i * 10,
            )

        # Filter by multiple criteria
        alice_gpt4_success = logger.get_logs(
            user="alice", model="gpt-4", status="success"
        )
        assert len(alice_gpt4_success) == 2  # i=0, i=12

        # Filter by correlation_id partial match
        # "complex-00" matches only complex-000 to complex-009 (10 entries)
        complex_00x = logger.get_logs(correlation_id="complex-00")
        assert len(complex_00x) == 10  # 000-009


class TestModelRouterIntegration:
    """Integration tests for ModelRouter with web portal."""

    def test_router_info_for_services_page(self):
        """Test that router provides info for services page."""
        router = ModelRouter()

        # Simulate connector registration
        connector_id = "test-connector-1"
        models = ["gpt-4", "gpt-3.5-turbo"]

        # Register callback (normally called by tunnel server)
        router.on_connector_registered(
            connector_id=connector_id,
            models=models,
            llm_api_key=None,
        )

        # Get connector info for display
        info = router.get_connector_info()

        assert len(info) == 1
        assert info[0]["id"] == connector_id
        assert info[0]["models"] == models
        assert info[0]["connected"] is True

    def test_router_multiple_connectors(self):
        """Test router with multiple connectors."""
        router = ModelRouter()

        # Register multiple connectors
        router.on_connector_registered("conn-1", ["model-a", "model-b"], None)
        router.on_connector_registered("conn-2", ["model-c"], None)
        router.on_connector_registered("conn-3", ["model-d", "model-e", "model-f"], None)

        info = router.get_connector_info()
        assert len(info) == 3

        # Check total models
        total_models = sum(len(c["models"]) for c in info)
        assert total_models == 6

        # Check available models property
        assert len(router.available_models) == 6
