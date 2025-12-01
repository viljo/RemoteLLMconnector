"""Unit tests for the broker admin module."""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
from aiohttp_session import SimpleCookieStorage, session_middleware

from remotellm.broker.admin import AdminHandler, RequestLog, RequestLogger
from remotellm.broker.connectors import Connector, ConnectorStatus, ConnectorStore
from remotellm.broker.preprompts import Preprompt, PrepromptStore
from remotellm.broker.router import ModelRouter
from remotellm.broker.users import User, UserRole, UserStore


class TestRequestLog:
    """Tests for RequestLog dataclass."""

    def test_request_log_creation(self):
        """Test creating a RequestLog instance."""
        timestamp = datetime.utcnow()
        log = RequestLog(
            timestamp=timestamp,
            correlation_id="req-123",
            user="testuser",
            model="gpt-4",
            status="success",
            duration_ms=150,
        )

        assert log.timestamp == timestamp
        assert log.correlation_id == "req-123"
        assert log.user == "testuser"
        assert log.model == "gpt-4"
        assert log.status == "success"
        assert log.duration_ms == 150

    def test_request_log_with_none_user(self):
        """Test RequestLog with None user (unauthenticated request)."""
        log = RequestLog(
            timestamp=datetime.utcnow(),
            correlation_id="req-456",
            user=None,
            model="llama3",
            status="error",
            duration_ms=50,
        )

        assert log.user is None
        assert log.model == "llama3"


class TestRequestLogger:
    """Tests for RequestLogger."""

    def test_init_default_max_logs(self):
        """Test initialization with default max_logs."""
        logger = RequestLogger()
        assert logger.max_logs == 100
        assert len(logger._logs) == 0

    def test_init_custom_max_logs(self):
        """Test initialization with custom max_logs."""
        logger = RequestLogger(max_logs=50)
        assert logger.max_logs == 50

    def test_log_request(self):
        """Test logging a request."""
        logger = RequestLogger()
        logger.log_request(
            correlation_id="req-1",
            user="alice",
            model="gpt-4",
            status="success",
            duration_ms=200,
        )

        logs = logger.get_logs()
        assert len(logs) == 1
        assert logs[0].correlation_id == "req-1"
        assert logs[0].user == "alice"
        assert logs[0].model == "gpt-4"
        assert logs[0].status == "success"
        assert logs[0].duration_ms == 200

    def test_log_request_ordering(self):
        """Test that logs are ordered newest first."""
        logger = RequestLogger()
        logger.log_request("req-1", "alice", "gpt-4", "success", 100)
        logger.log_request("req-2", "bob", "llama3", "error", 50)

        logs = logger.get_logs()
        assert logs[0].correlation_id == "req-2"  # Most recent first
        assert logs[1].correlation_id == "req-1"

    def test_log_request_max_logs_limit(self):
        """Test that logger respects max_logs limit."""
        logger = RequestLogger(max_logs=3)

        for i in range(5):
            logger.log_request(f"req-{i}", "user", "model", "success", 100)

        logs = logger.get_logs()
        assert len(logs) == 3
        # Should keep only the 3 most recent
        assert logs[0].correlation_id == "req-4"
        assert logs[1].correlation_id == "req-3"
        assert logs[2].correlation_id == "req-2"

    def test_get_logs_filter_by_user(self):
        """Test filtering logs by user."""
        logger = RequestLogger()
        logger.log_request("req-1", "alice", "gpt-4", "success", 100)
        logger.log_request("req-2", "bob", "llama3", "success", 100)
        logger.log_request("req-3", "alice", "gpt-3.5", "error", 50)

        logs = logger.get_logs(user="alice")
        assert len(logs) == 2
        assert all(log.user == "alice" for log in logs)

    def test_get_logs_filter_by_model(self):
        """Test filtering logs by model."""
        logger = RequestLogger()
        logger.log_request("req-1", "alice", "gpt-4", "success", 100)
        logger.log_request("req-2", "bob", "gpt-4", "success", 100)
        logger.log_request("req-3", "alice", "llama3", "error", 50)

        logs = logger.get_logs(model="gpt-4")
        assert len(logs) == 2
        assert all(log.model == "gpt-4" for log in logs)

    def test_get_logs_filter_by_status(self):
        """Test filtering logs by status."""
        logger = RequestLogger()
        logger.log_request("req-1", "alice", "gpt-4", "success", 100)
        logger.log_request("req-2", "bob", "llama3", "error", 50)
        logger.log_request("req-3", "alice", "gpt-3.5", "error", 50)

        logs = logger.get_logs(status="error")
        assert len(logs) == 2
        assert all(log.status == "error" for log in logs)

    def test_get_logs_filter_by_correlation_id(self):
        """Test filtering logs by correlation_id."""
        logger = RequestLogger()
        logger.log_request("req-abc-1", "alice", "gpt-4", "success", 100)
        logger.log_request("req-xyz-2", "bob", "llama3", "success", 100)
        logger.log_request("req-abc-3", "alice", "gpt-3.5", "error", 50)

        # Should match partial correlation_id
        logs = logger.get_logs(correlation_id="abc")
        assert len(logs) == 2
        assert all("abc" in log.correlation_id for log in logs)

    def test_get_logs_multiple_filters(self):
        """Test filtering logs with multiple criteria."""
        logger = RequestLogger()
        logger.log_request("req-1", "alice", "gpt-4", "success", 100)
        logger.log_request("req-2", "alice", "gpt-4", "error", 50)
        logger.log_request("req-3", "bob", "gpt-4", "success", 100)

        logs = logger.get_logs(user="alice", status="success")
        assert len(logs) == 1
        assert logs[0].correlation_id == "req-1"

    def test_get_logs_no_filters(self):
        """Test getting all logs with no filters."""
        logger = RequestLogger()
        logger.log_request("req-1", "alice", "gpt-4", "success", 100)
        logger.log_request("req-2", "bob", "llama3", "error", 50)

        logs = logger.get_logs()
        assert len(logs) == 2


class TestAdminHandlerInit:
    """Tests for AdminHandler initialization."""

    def test_basic_init(self):
        """Test basic initialization."""
        user_store = UserStore()
        router = ModelRouter()
        request_logger = RequestLogger()

        handler = AdminHandler(
            user_store=user_store,
            router=router,
            request_logger=request_logger,
        )

        assert handler.user_store is user_store
        assert handler.router is router
        assert handler.request_logger is request_logger
        assert handler.preprompt_store is None
        assert handler.connector_store is None
        assert handler.relay_server is None

    def test_init_with_all_stores(self):
        """Test initialization with all optional stores."""
        user_store = UserStore()
        router = ModelRouter()
        request_logger = RequestLogger()
        preprompt_store = PrepromptStore()
        connector_store = ConnectorStore()
        relay_server = MagicMock()

        handler = AdminHandler(
            user_store=user_store,
            router=router,
            request_logger=request_logger,
            preprompt_store=preprompt_store,
            connector_store=connector_store,
            relay_server=relay_server,
        )

        assert handler.preprompt_store is preprompt_store
        assert handler.connector_store is connector_store
        assert handler.relay_server is relay_server


class TestAdminRequireAuth(AioHTTPTestCase):
    """Tests for _require_admin authentication."""

    async def get_application(self):
        """Create the test application with session middleware."""
        self.user_store = UserStore()
        self.router = ModelRouter()
        self.request_logger = RequestLogger()

        self.handler = AdminHandler(
            user_store=self.user_store,
            router=self.router,
            request_logger=self.request_logger,
        )

        app = web.Application(
            middlewares=[
                session_middleware(SimpleCookieStorage()),
            ]
        )
        self.handler.setup_routes(app)
        return app

    @unittest_run_loop
    async def test_require_admin_no_session(self):
        """Test _require_admin with no session redirects to login."""
        # Without session, should raise HTTPFound
        request = MagicMock()
        session_data = {}

        with pytest.raises(web.HTTPFound):
            with patch("remotellm.broker.admin.get_session", AsyncMock(return_value=session_data)):
                await self.handler._require_admin(request)

    @unittest_run_loop
    async def test_require_admin_non_admin_user(self):
        """Test _require_admin with non-admin user returns forbidden."""
        # Create a regular user
        user = self.user_store.create_user("testuser", 123, UserRole.USER)

        # Create session manually
        request = MagicMock()
        session_data = {
            "user": {
                "username": "testuser",
                "role": "user",
            }
        }

        # Test using the handler method directly
        with pytest.raises(web.HTTPForbidden):
            # Mock get_session
            with patch("remotellm.broker.admin.get_session", AsyncMock(return_value=session_data)):
                await self.handler._require_admin(request)

    @unittest_run_loop
    async def test_require_admin_blocked_user(self):
        """Test _require_admin with blocked admin user redirects to login."""
        # Create admin user and block them
        user = self.user_store.create_user("admin", 456, UserRole.ADMIN)
        self.user_store.set_blocked("admin", True)

        request = MagicMock()
        session_mock = MagicMock()
        session_mock.get.return_value = {
            "username": "admin",
            "role": "admin",
        }
        session_mock.clear = MagicMock()

        with pytest.raises(web.HTTPFound):
            with patch("remotellm.broker.admin.get_session", AsyncMock(return_value=session_mock)):
                await self.handler._require_admin(request)

        # Verify session was cleared
        session_mock.clear.assert_called_once()


class TestAdminDashboard(AioHTTPTestCase):
    """Tests for admin dashboard endpoint."""

    async def get_application(self):
        """Create the test application."""
        self.user_store = UserStore()
        self.router = ModelRouter()
        self.request_logger = RequestLogger()

        # Create an admin user
        self.admin_user = self.user_store.create_user("admin", 1, UserRole.ADMIN)

        self.handler = AdminHandler(
            user_store=self.user_store,
            router=self.router,
            request_logger=self.request_logger,
        )

        app = web.Application(
            middlewares=[
                session_middleware(SimpleCookieStorage()),
            ]
        )

        # Mock template rendering
        with patch("aiohttp_jinja2.render_template") as mock_render:
            mock_render.return_value = web.Response(text="mocked")
            self.handler.setup_routes(app)

        return app

    @unittest_run_loop
    async def test_dashboard_stats(self):
        """Test dashboard returns correct statistics."""
        # Add some test data
        self.user_store.create_user("user1", 2, UserRole.USER)
        self.user_store.create_user("user2", 3, UserRole.USER)
        self.router.on_connector_registered("conn-1", ["gpt-4", "llama3"], None)

        # Mock the template render to capture context
        with patch("aiohttp_jinja2.render_template") as mock_render:
            mock_render.return_value = web.Response(text="dashboard")

            # Mock session
            request = MagicMock()
            request.app = self.app
            session_data = {
                "user": {
                    "username": "admin",
                    "role": "admin",
                }
            }

            with patch("remotellm.broker.admin.get_session", AsyncMock(return_value=session_data)):
                await self.handler.handle_dashboard(request)

            # Verify template was called
            assert mock_render.called
            context = mock_render.call_args[0][2]

            # Check stats
            assert context["stats"]["user_count"] == 3  # admin + 2 users
            assert context["stats"]["connector_count"] == 1
            assert context["stats"]["model_count"] == 2
            assert context["stats"]["admin_count"] == 1


class TestAdminUsers(AioHTTPTestCase):
    """Tests for user management endpoints."""

    async def get_application(self):
        """Create the test application."""
        self.user_store = UserStore()
        self.router = ModelRouter()
        self.request_logger = RequestLogger()
        self.admin_user = self.user_store.create_user("admin", 1, UserRole.ADMIN)

        self.handler = AdminHandler(
            user_store=self.user_store,
            router=self.router,
            request_logger=self.request_logger,
        )

        app = web.Application(
            middlewares=[
                session_middleware(SimpleCookieStorage()),
            ]
        )
        self.handler.setup_routes(app)
        return app

    def _mock_admin_session(self):
        """Helper to mock admin session."""
        session_data = {
            "user": {
                "username": "admin",
                "role": "admin",
            }
        }
        return patch("remotellm.broker.admin.get_session", AsyncMock(return_value=session_data))

    @unittest_run_loop
    async def test_add_user_success(self):
        """Test adding a new user successfully."""
        with self._mock_admin_session():
            # Create form data
            response = await self.client.post(
                "/admin/users/add",
                data={
                    "gitlab_username": "newuser",
                    "gitlab_id": "999",
                },
                allow_redirects=False,
            )

            # Should redirect (302 or similar)
            assert response.status in (200, 302, 303, 307)

            # Verify user was created
            user = self.user_store.get_by_username("newuser")
            assert user is not None
            assert user.gitlab_id == 999

    async def test_add_user_missing_fields(self):
        """Test adding user with missing fields."""
        request = MagicMock()
        request.post = AsyncMock(return_value={"gitlab_username": "", "gitlab_id": ""})

        with self._mock_admin_session():
            with pytest.raises(web.HTTPFound) as exc_info:
                await self.handler.handle_add_user(request)
            # Should redirect with error message
            assert "required" in exc_info.value.location.lower()

    async def test_add_user_invalid_id(self):
        """Test adding user with invalid GitLab ID."""
        request = MagicMock()
        request.post = AsyncMock(return_value={"gitlab_username": "newuser", "gitlab_id": "not-a-number"})

        with self._mock_admin_session():
            with pytest.raises(web.HTTPFound) as exc_info:
                await self.handler.handle_add_user(request)
            # Should redirect with error
            assert "invalid" in exc_info.value.location.lower()

        user = self.user_store.get_by_username("newuser")
        assert user is None

    async def test_add_user_duplicate(self):
        """Test adding a duplicate user."""
        self.user_store.create_user("existing", 123, UserRole.USER)

        request = MagicMock()
        request.post = AsyncMock(return_value={"gitlab_username": "existing", "gitlab_id": "456"})

        with self._mock_admin_session():
            with pytest.raises(web.HTTPFound) as exc_info:
                await self.handler.handle_add_user(request)
            # Should redirect with error
            assert "exists" in exc_info.value.location.lower()

        # User count should still be 2 (admin + existing)
        assert len(self.user_store.get_all()) == 2

    async def test_toggle_block_user(self):
        """Test blocking and unblocking a user."""
        user = self.user_store.create_user("testuser", 123, UserRole.USER)
        assert not user.blocked

        request = MagicMock()
        request.match_info = {"username": "testuser"}

        with self._mock_admin_session():
            # Block the user
            with pytest.raises(web.HTTPFound):
                await self.handler.handle_toggle_block(request)

            # Verify user is blocked
            user = self.user_store.get_by_username("testuser")
            assert user.blocked

            # Unblock the user
            with pytest.raises(web.HTTPFound):
                await self.handler.handle_toggle_block(request)

            # Verify user is unblocked
            user = self.user_store.get_by_username("testuser")
            assert not user.blocked

    async def test_toggle_block_nonexistent_user(self):
        """Test blocking a user that doesn't exist."""
        request = MagicMock()
        request.match_info = {"username": "nonexistent"}

        with self._mock_admin_session():
            with pytest.raises(web.HTTPFound) as exc_info:
                await self.handler.handle_toggle_block(request)
            assert "not+found" in exc_info.value.location or "error" in exc_info.value.location

    async def test_toggle_role_user(self):
        """Test promoting and demoting a user."""
        user = self.user_store.create_user("testuser", 123, UserRole.USER)
        assert user.role == UserRole.USER

        request = MagicMock()
        request.match_info = {"username": "testuser"}

        with self._mock_admin_session():
            # Promote to admin
            with pytest.raises(web.HTTPFound):
                await self.handler.handle_toggle_role(request)

            user = self.user_store.get_by_username("testuser")
            assert user.role == UserRole.ADMIN

            # Demote to user
            with pytest.raises(web.HTTPFound):
                await self.handler.handle_toggle_role(request)

            user = self.user_store.get_by_username("testuser")
            assert user.role == UserRole.USER

    async def test_toggle_role_nonexistent_user(self):
        """Test toggling role for nonexistent user."""
        request = MagicMock()
        request.match_info = {"username": "nonexistent"}

        with self._mock_admin_session():
            with pytest.raises(web.HTTPFound) as exc_info:
                await self.handler.handle_toggle_role(request)
            assert "not+found" in exc_info.value.location or "error" in exc_info.value.location


class TestAdminLogs(AioHTTPTestCase):
    """Tests for admin logs endpoint."""

    async def get_application(self):
        """Create the test application."""
        self.user_store = UserStore()
        self.router = ModelRouter()
        self.request_logger = RequestLogger()
        self.admin_user = self.user_store.create_user("admin", 1, UserRole.ADMIN)

        # Add some test logs
        self.request_logger.log_request("req-1", "alice", "gpt-4", "success", 100)
        self.request_logger.log_request("req-2", "bob", "llama3", "error", 50)
        self.request_logger.log_request("req-3", "alice", "gpt-4", "success", 150)

        self.handler = AdminHandler(
            user_store=self.user_store,
            router=self.router,
            request_logger=self.request_logger,
        )

        app = web.Application(
            middlewares=[
                session_middleware(SimpleCookieStorage()),
            ]
        )
        self.handler.setup_routes(app)
        return app

    @unittest_run_loop
    async def test_logs_endpoint(self):
        """Test logs endpoint returns logged requests."""
        with patch("aiohttp_jinja2.render_template") as mock_render:
            mock_render.return_value = web.Response(text="logs")

            request = MagicMock()
            request.app = self.app
            request.query = {}
            session_data = {"user": {"username": "admin", "role": "admin"}}

            with patch("remotellm.broker.admin.get_session", AsyncMock(return_value=session_data)):
                await self.handler.handle_logs(request)

            assert mock_render.called
            context = mock_render.call_args[0][2]
            assert len(context["logs"]) == 3

    @unittest_run_loop
    async def test_logs_filtering(self):
        """Test logs endpoint with filters."""
        with patch("aiohttp_jinja2.render_template") as mock_render:
            mock_render.return_value = web.Response(text="logs")

            request = MagicMock()
            request.app = self.app
            request.query = {"user": "alice", "status": "success"}
            session_data = {"user": {"username": "admin", "role": "admin"}}

            with patch("remotellm.broker.admin.get_session", AsyncMock(return_value=session_data)):
                await self.handler.handle_logs(request)

            assert mock_render.called
            context = mock_render.call_args[0][2]
            # Should only return alice's successful requests
            assert len(context["logs"]) == 2
            assert all(log.user == "alice" for log in context["logs"])
            assert all(log.status == "success" for log in context["logs"])


class TestAdminConnectors(AioHTTPTestCase):
    """Tests for connector management endpoints."""

    async def get_application(self):
        """Create the test application."""
        self.user_store = UserStore()
        self.router = ModelRouter()
        self.request_logger = RequestLogger()
        self.connector_store = ConnectorStore()
        self.relay_server = MagicMock()
        self.relay_server.is_connector_connected = MagicMock(return_value=False)
        self.relay_server.notify_approval = AsyncMock()
        self.relay_server.notify_revoke = AsyncMock()

        self.admin_user = self.user_store.create_user("admin", 1, UserRole.ADMIN)

        self.handler = AdminHandler(
            user_store=self.user_store,
            router=self.router,
            request_logger=self.request_logger,
            connector_store=self.connector_store,
            relay_server=self.relay_server,
        )

        app = web.Application(
            middlewares=[
                session_middleware(SimpleCookieStorage()),
            ]
        )
        self.handler.setup_routes(app)
        return app

    def _mock_admin_session(self):
        """Helper to mock admin session."""
        session_data = {"user": {"username": "admin", "role": "admin"}}
        return patch("remotellm.broker.admin.get_session", AsyncMock(return_value=session_data))

    @unittest_run_loop
    async def test_connectors_page(self):
        """Test connectors management page."""
        # Create test connectors
        pending = self.connector_store.create_pending(["gpt-4"], "Test Connector 1")
        approved = self.connector_store.create_pending(["llama3"], "Test Connector 2")
        self.connector_store.approve(approved.connector_id)

        with patch("aiohttp_jinja2.render_template") as mock_render:
            mock_render.return_value = web.Response(text="connectors")

            request = MagicMock()
            request.app = self.app
            request.query = {}

            with self._mock_admin_session():
                await self.handler.handle_connectors(request)

            assert mock_render.called
            context = mock_render.call_args[0][2]
            assert len(context["pending_connectors"]) == 1
            assert len(context["approved_connectors"]) == 1

    @unittest_run_loop
    async def test_connectors_without_store(self):
        """Test connectors page when store not configured."""
        # Create handler without connector store
        handler = AdminHandler(
            user_store=self.user_store,
            router=self.router,
            request_logger=self.request_logger,
            connector_store=None,
        )

        request = MagicMock()
        request.app = self.app
        request.query = {}

        with self._mock_admin_session():
            with pytest.raises(web.HTTPFound):
                await handler.handle_connectors(request)

    async def test_approve_connector(self):
        """Test approving a pending connector."""
        connector = self.connector_store.create_pending(["gpt-4"], "Test")

        request = MagicMock()
        request.match_info = {"connector_id": connector.connector_id}

        with self._mock_admin_session():
            with pytest.raises(web.HTTPFound):
                await self.handler.handle_approve_connector(request)

            # Verify connector was approved
            updated = self.connector_store.get_by_id(connector.connector_id)
            assert updated.status == ConnectorStatus.APPROVED
            assert updated.api_key is not None

            # Verify relay server was notified
            self.relay_server.notify_approval.assert_called_once()

    async def test_approve_nonexistent_connector(self):
        """Test approving a connector that doesn't exist."""
        request = MagicMock()
        request.match_info = {"connector_id": "nonexistent"}

        with self._mock_admin_session():
            with pytest.raises(web.HTTPFound) as exc_info:
                await self.handler.handle_approve_connector(request)
            assert "not+found" in exc_info.value.location or "error" in exc_info.value.location

    async def test_approve_already_approved_connector(self):
        """Test approving an already approved connector."""
        connector = self.connector_store.create_pending(["gpt-4"], "Test")
        self.connector_store.approve(connector.connector_id)

        request = MagicMock()
        request.match_info = {"connector_id": connector.connector_id}

        with self._mock_admin_session():
            with pytest.raises(web.HTTPFound) as exc_info:
                await self.handler.handle_approve_connector(request)
            # Should redirect with error
            assert "not+found" in exc_info.value.location or "pending" in exc_info.value.location

    async def test_revoke_connector(self):
        """Test revoking a connector."""
        connector = self.connector_store.create_pending(["gpt-4"], "Test")
        self.connector_store.approve(connector.connector_id)

        request = MagicMock()
        request.match_info = {"connector_id": connector.connector_id}

        with self._mock_admin_session():
            with pytest.raises(web.HTTPFound):
                await self.handler.handle_revoke_connector(request)

            # Verify connector was revoked
            updated = self.connector_store.get_by_id(connector.connector_id)
            assert updated.status == ConnectorStatus.REVOKED

            # Verify relay server was notified
            self.relay_server.notify_revoke.assert_called_once()

    async def test_revoke_nonexistent_connector(self):
        """Test revoking a connector that doesn't exist."""
        request = MagicMock()
        request.match_info = {"connector_id": "nonexistent"}

        with self._mock_admin_session():
            with pytest.raises(web.HTTPFound) as exc_info:
                await self.handler.handle_revoke_connector(request)
            assert "not+found" in exc_info.value.location or "error" in exc_info.value.location

    async def test_delete_connector(self):
        """Test deleting a connector."""
        connector = self.connector_store.create_pending(["gpt-4"], "Test")

        request = MagicMock()
        request.match_info = {"connector_id": connector.connector_id}

        with self._mock_admin_session():
            with pytest.raises(web.HTTPFound):
                await self.handler.handle_delete_connector(request)

            # Verify connector was deleted
            deleted = self.connector_store.get_by_id(connector.connector_id)
            assert deleted is None

            # Verify relay server was notified
            self.relay_server.notify_revoke.assert_called_once()

    async def test_delete_nonexistent_connector(self):
        """Test deleting a connector that doesn't exist."""
        request = MagicMock()
        request.match_info = {"connector_id": "nonexistent"}

        with self._mock_admin_session():
            with pytest.raises(web.HTTPFound) as exc_info:
                await self.handler.handle_delete_connector(request)
            assert "not+found" in exc_info.value.location or "error" in exc_info.value.location

    @unittest_run_loop
    async def test_connectors_connection_status(self):
        """Test that connector connection status is shown."""
        connector = self.connector_store.create_pending(["gpt-4"], "Test")
        self.connector_store.approve(connector.connector_id)

        # Mock one connector as connected
        self.relay_server.is_connector_connected = MagicMock(
            side_effect=lambda cid: cid == connector.connector_id
        )

        with patch("aiohttp_jinja2.render_template") as mock_render:
            mock_render.return_value = web.Response(text="connectors")

            request = MagicMock()
            request.app = self.app
            request.query = {}

            with self._mock_admin_session():
                await self.handler.handle_connectors(request)

            context = mock_render.call_args[0][2]
            # Check that connection status was included
            assert context["approved_connectors"][0]["is_connected"] is True


class TestAdminSettings(AioHTTPTestCase):
    """Tests for settings management endpoints."""

    async def get_application(self):
        """Create the test application."""
        self.user_store = UserStore()
        self.router = ModelRouter()
        self.request_logger = RequestLogger()
        self.preprompt_store = PrepromptStore()
        self.admin_user = self.user_store.create_user("admin", 1, UserRole.ADMIN)

        self.handler = AdminHandler(
            user_store=self.user_store,
            router=self.router,
            request_logger=self.request_logger,
            preprompt_store=self.preprompt_store,
        )

        app = web.Application(
            middlewares=[
                session_middleware(SimpleCookieStorage()),
            ]
        )
        self.handler.setup_routes(app)
        return app

    def _mock_admin_session(self):
        """Helper to mock admin session."""
        session_data = {"user": {"username": "admin", "role": "admin"}}
        return patch("remotellm.broker.admin.get_session", AsyncMock(return_value=session_data))

    @unittest_run_loop
    async def test_settings_page(self):
        """Test settings page displays preprompts."""
        self.preprompt_store.create_or_update(
            "default",
            "You are a helpful assistant.",
            is_default=True,
        )

        with patch("aiohttp_jinja2.render_template") as mock_render:
            mock_render.return_value = web.Response(text="settings")

            request = MagicMock()
            request.app = self.app
            request.query = {}

            with self._mock_admin_session():
                await self.handler.handle_settings(request)

            assert mock_render.called
            context = mock_render.call_args[0][2]
            assert len(context["preprompts"]) == 1
            assert context["default_preprompt"].name == "default"

    async def test_save_preprompt(self):
        """Test saving a new preprompt."""
        request = MagicMock()
        request.post = AsyncMock(return_value={
            "action": "save",
            "name": "helpful",
            "content": "You are helpful.",
            "is_default": "on",
        })

        with self._mock_admin_session():
            with pytest.raises(web.HTTPFound):
                await self.handler.handle_save_settings(request)

            # Verify preprompt was saved
            preprompt = self.preprompt_store.get_by_name("helpful")
            assert preprompt is not None
            assert preprompt.content == "You are helpful."
            assert preprompt.is_default is True

    async def test_save_preprompt_without_store(self):
        """Test saving preprompt when store not configured."""
        handler = AdminHandler(
            user_store=self.user_store,
            router=self.router,
            request_logger=self.request_logger,
            preprompt_store=None,
        )

        request = MagicMock()
        request.post = AsyncMock(return_value={"action": "save"})

        with self._mock_admin_session():
            with pytest.raises(web.HTTPFound):
                await handler.handle_save_settings(request)

    async def test_save_preprompt_without_name(self):
        """Test saving preprompt without name."""
        request = MagicMock()
        request.post = AsyncMock(return_value={
            "action": "save",
            "name": "",
            "content": "Test content",
        })

        with self._mock_admin_session():
            with pytest.raises(web.HTTPFound) as exc_info:
                await self.handler.handle_save_settings(request)
            assert "required" in exc_info.value.location.lower()

    async def test_delete_preprompt(self):
        """Test deleting a preprompt."""
        self.preprompt_store.create_or_update("test", "Test content")

        request = MagicMock()
        request.post = AsyncMock(return_value={
            "action": "delete",
            "name": "test",
        })

        with self._mock_admin_session():
            with pytest.raises(web.HTTPFound):
                await self.handler.handle_save_settings(request)

            # Verify preprompt was deleted
            preprompt = self.preprompt_store.get_by_name("test")
            assert preprompt is None

    async def test_delete_nonexistent_preprompt(self):
        """Test deleting a preprompt that doesn't exist."""
        request = MagicMock()
        request.post = AsyncMock(return_value={
            "action": "delete",
            "name": "nonexistent",
        })

        with self._mock_admin_session():
            with pytest.raises(web.HTTPFound) as exc_info:
                await self.handler.handle_save_settings(request)
            assert "not+found" in exc_info.value.location or "error" in exc_info.value.location

    async def test_set_default_preprompt(self):
        """Test setting a preprompt as default."""
        self.preprompt_store.create_or_update("first", "First", is_default=True)
        self.preprompt_store.create_or_update("second", "Second", is_default=False)

        request = MagicMock()
        request.post = AsyncMock(return_value={
            "action": "set_default",
            "name": "second",
        })

        with self._mock_admin_session():
            with pytest.raises(web.HTTPFound):
                await self.handler.handle_save_settings(request)

            # Verify second is now default
            first = self.preprompt_store.get_by_name("first")
            second = self.preprompt_store.get_by_name("second")
            assert first.is_default is False
            assert second.is_default is True

    async def test_set_default_nonexistent_preprompt(self):
        """Test setting nonexistent preprompt as default."""
        request = MagicMock()
        request.post = AsyncMock(return_value={
            "action": "set_default",
            "name": "nonexistent",
        })

        with self._mock_admin_session():
            with pytest.raises(web.HTTPFound) as exc_info:
                await self.handler.handle_save_settings(request)
            assert "not+found" in exc_info.value.location or "error" in exc_info.value.location


class TestAdminErrorHandling(AioHTTPTestCase):
    """Tests for error handling in admin routes."""

    async def get_application(self):
        """Create the test application."""
        self.user_store = UserStore()
        self.router = ModelRouter()
        self.request_logger = RequestLogger()
        self.admin_user = self.user_store.create_user("admin", 1, UserRole.ADMIN)

        self.handler = AdminHandler(
            user_store=self.user_store,
            router=self.router,
            request_logger=self.request_logger,
        )

        app = web.Application(
            middlewares=[
                session_middleware(SimpleCookieStorage()),
            ]
        )
        self.handler.setup_routes(app)
        return app

    async def test_unauthenticated_access(self):
        """Test that unauthenticated users are redirected."""
        # Without session, should raise HTTPFound redirect
        request = MagicMock()
        session_data = {}

        with patch("remotellm.broker.admin.get_session", AsyncMock(return_value=session_data)):
            with pytest.raises(web.HTTPFound):
                await self.handler._require_admin(request)

    @unittest_run_loop
    async def test_non_admin_user_access(self):
        """Test that non-admin users get forbidden."""
        regular_user = self.user_store.create_user("user", 2, UserRole.USER)

        request = MagicMock()
        session_data = {"user": {"username": "user", "role": "user"}}

        with patch("remotellm.broker.admin.get_session", AsyncMock(return_value=session_data)):
            with pytest.raises(web.HTTPForbidden):
                await self.handler._require_admin(request)

    @unittest_run_loop
    async def test_deleted_user_access(self):
        """Test that deleted user session is cleared."""
        # Create user and get session data
        user = self.user_store.create_user("tempuser", 999, UserRole.ADMIN)

        # Delete the user
        self.user_store.delete_user("tempuser")

        request = MagicMock()
        session_mock = MagicMock()
        session_mock.get.return_value = {"username": "tempuser", "role": "admin"}
        session_mock.clear = MagicMock()

        with patch("remotellm.broker.admin.get_session", AsyncMock(return_value=session_mock)):
            with pytest.raises(web.HTTPFound):
                await self.handler._require_admin(request)

        # Session should be cleared
        session_mock.clear.assert_called_once()
