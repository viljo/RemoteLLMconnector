"""Admin dashboard handlers for the broker web portal."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import aiohttp_jinja2
import structlog
from aiohttp import web
from aiohttp_session import get_session

from .connectors import ConnectorStore
from .preprompts import PrepromptStore
from .users import UserRole, UserStore

if TYPE_CHECKING:
    from .router import ModelRouter
    from .relay_server import RelayServer

log = structlog.get_logger()


@dataclass
class RequestLog:
    """A logged API request."""

    timestamp: datetime
    correlation_id: str
    user: str | None
    model: str
    status: str
    duration_ms: int


class RequestLogger:
    """In-memory request logger with limited history."""

    def __init__(self, max_logs: int = 100) -> None:
        """Initialize the request logger.

        Args:
            max_logs: Maximum number of logs to keep in memory.
        """
        self.max_logs = max_logs
        self._logs: deque[RequestLog] = deque(maxlen=max_logs)

    def log_request(
        self,
        correlation_id: str,
        user: str | None,
        model: str,
        status: str,
        duration_ms: int,
    ) -> None:
        """Log an API request."""
        self._logs.appendleft(
            RequestLog(
                timestamp=datetime.utcnow(),
                correlation_id=correlation_id,
                user=user,
                model=model,
                status=status,
                duration_ms=duration_ms,
            )
        )

    def get_logs(
        self,
        user: str | None = None,
        model: str | None = None,
        status: str | None = None,
        correlation_id: str | None = None,
    ) -> list[RequestLog]:
        """Get filtered logs."""
        logs = list(self._logs)

        if user:
            logs = [entry for entry in logs if entry.user == user]
        if model:
            logs = [entry for entry in logs if entry.model == model]
        if status:
            logs = [entry for entry in logs if entry.status == status]
        if correlation_id:
            logs = [entry for entry in logs if correlation_id in entry.correlation_id]

        return logs


class AdminHandler:
    """Handler for admin dashboard pages."""

    def __init__(
        self,
        user_store: UserStore,
        router: "ModelRouter",
        request_logger: RequestLogger,
        preprompt_store: PrepromptStore | None = None,
        connector_store: ConnectorStore | None = None,
        relay_server: "RelayServer | None" = None,
    ) -> None:
        """Initialize the admin handler.

        Args:
            user_store: User storage instance
            router: Model router for getting connector info
            request_logger: Request logger for viewing logs
            preprompt_store: Preprompt storage instance
            connector_store: Connector storage for approval workflow
            relay_server: Relay server for sending approval/revoke notifications
        """
        self.user_store = user_store
        self.router = router
        self.request_logger = request_logger
        self.preprompt_store = preprompt_store
        self.connector_store = connector_store
        self.relay_server = relay_server

    def setup_routes(self, app: web.Application) -> None:
        """Register admin routes with the application."""
        app.router.add_get("/admin", self.handle_dashboard)
        app.router.add_get("/admin/users", self.handle_users)
        app.router.add_post("/admin/users/add", self.handle_add_user)
        app.router.add_post("/admin/users/{username}/toggle-block", self.handle_toggle_block)
        app.router.add_post("/admin/users/{username}/toggle-role", self.handle_toggle_role)
        app.router.add_get("/admin/logs", self.handle_logs)
        app.router.add_get("/admin/settings", self.handle_settings)
        app.router.add_post("/admin/settings", self.handle_save_settings)
        # Connector management routes
        app.router.add_get("/admin/connectors", self.handle_connectors)
        app.router.add_post("/admin/connectors/{connector_id}/approve", self.handle_approve_connector)
        app.router.add_post("/admin/connectors/{connector_id}/revoke", self.handle_revoke_connector)
        app.router.add_post("/admin/connectors/{connector_id}/delete", self.handle_delete_connector)

    async def _require_admin(self, request: web.Request) -> tuple[dict, UserStore]:
        """Require admin access and return session user data."""
        session = await get_session(request)
        user_data = session.get("user")

        if not user_data:
            raise web.HTTPFound("/auth/login")

        if user_data.get("role") != "admin":
            raise web.HTTPForbidden(text="Admin access required")

        # Verify user still exists and is admin
        user = self.user_store.get_by_username(user_data["username"])
        if user is None or user.blocked or user.role != UserRole.ADMIN:
            session.clear()
            raise web.HTTPFound("/auth/login")

        return user_data, user

    async def handle_dashboard(self, request: web.Request) -> web.Response:
        """Handle the admin dashboard page."""
        user_data, user = await self._require_admin(request)

        # Get stats
        all_users = self.user_store.get_all()
        connectors = self.router.get_connector_info()

        stats = {
            "user_count": len(all_users),
            "connector_count": len(connectors),
            "model_count": len(self.router.available_models),
            "admin_count": sum(1 for u in all_users if u.role == UserRole.ADMIN),
        }

        # Get recent users sorted by last_used
        recent_users = sorted(
            [u for u in all_users if u.last_used],
            key=lambda u: u.last_used,
            reverse=True,
        )

        context = {
            "request": request,
            "user": user,
            "is_admin": True,
            "stats": stats,
            "connectors": connectors,
            "recent_users": recent_users,
        }
        return aiohttp_jinja2.render_template("admin/dashboard.html", request, context)

    async def handle_users(self, request: web.Request) -> web.Response:
        """Handle the user management page."""
        user_data, user = await self._require_admin(request)

        all_users = self.user_store.get_all()

        # Sort by created_at, newest first
        all_users = sorted(all_users, key=lambda u: u.created_at, reverse=True)

        # Get message from query params (for feedback after actions)
        message = request.query.get("message")
        message_type = request.query.get("type", "success")

        context = {
            "request": request,
            "user": user,
            "is_admin": True,
            "users": all_users,
            "message": message,
            "message_type": message_type,
        }
        return aiohttp_jinja2.render_template("admin/users.html", request, context)

    async def handle_add_user(self, request: web.Request) -> web.Response:
        """Handle adding a new user."""
        await self._require_admin(request)

        data = await request.post()
        gitlab_username = data.get("gitlab_username", "").strip()
        gitlab_id_str = data.get("gitlab_id", "").strip()

        if not gitlab_username or not gitlab_id_str:
            raise web.HTTPFound("/admin/users?message=Username+and+ID+required&type=error")

        try:
            gitlab_id = int(gitlab_id_str)
        except ValueError:
            raise web.HTTPFound("/admin/users?message=Invalid+GitLab+ID&type=error") from None

        # Check if user already exists
        if self.user_store.get_by_username(gitlab_username):
            raise web.HTTPFound(
                f"/admin/users?message=User+{gitlab_username}+already+exists&type=error"
            )

        # Create user
        self.user_store.create_user(gitlab_username, gitlab_id)
        log.info("Admin added user manually", username=gitlab_username, gitlab_id=gitlab_id)

        raise web.HTTPFound(f"/admin/users?message=User+{gitlab_username}+created&type=success")

    async def handle_toggle_block(self, request: web.Request) -> web.Response:
        """Handle blocking/unblocking a user."""
        await self._require_admin(request)

        username = request.match_info["username"]
        user = self.user_store.get_by_username(username)

        if user is None:
            raise web.HTTPFound("/admin/users?message=User+not+found&type=error")

        # Toggle blocked status
        new_status = not user.blocked
        self.user_store.set_blocked(username, new_status)

        action = "blocked" if new_status else "unblocked"
        log.info(f"Admin {action} user", username=username)

        raise web.HTTPFound(f"/admin/users?message=User+{username}+{action}&type=success")

    async def handle_toggle_role(self, request: web.Request) -> web.Response:
        """Handle promoting/demoting a user."""
        await self._require_admin(request)

        username = request.match_info["username"]
        user = self.user_store.get_by_username(username)

        if user is None:
            raise web.HTTPFound("/admin/users?message=User+not+found&type=error")

        # Toggle role
        new_role = UserRole.USER if user.role == UserRole.ADMIN else UserRole.ADMIN
        self.user_store.set_role(username, new_role)

        action = "promoted to admin" if new_role == UserRole.ADMIN else "demoted to user"
        log.info("Admin changed user role", username=username, new_role=new_role.value)

        raise web.HTTPFound(f"/admin/users?message=User+{username}+{action}&type=success")

    async def handle_logs(self, request: web.Request) -> web.Response:
        """Handle the logs page."""
        user_data, user = await self._require_admin(request)

        # Get filter params
        filters = {
            "user": request.query.get("user"),
            "model": request.query.get("model"),
            "status": request.query.get("status"),
            "correlation_id": request.query.get("correlation_id"),
        }

        # Get filtered logs
        logs = self.request_logger.get_logs(
            user=filters["user"] or None,
            model=filters["model"] or None,
            status=filters["status"] or None,
            correlation_id=filters["correlation_id"] or None,
        )

        context = {
            "request": request,
            "user": user,
            "is_admin": True,
            "logs": logs,
            "filters": filters,
            "all_users": self.user_store.get_all(),
            "all_models": self.router.available_models,
            "max_logs": self.request_logger.max_logs,
        }
        return aiohttp_jinja2.render_template("admin/logs.html", request, context)

    async def handle_settings(self, request: web.Request) -> web.Response:
        """Handle the settings page."""
        user_data, user = await self._require_admin(request)

        # Get message from query params (for feedback after actions)
        message = request.query.get("message")
        message_type = request.query.get("type", "success")

        # Get preprompts if store is available
        preprompts = []
        default_preprompt = None
        if self.preprompt_store:
            preprompts = self.preprompt_store.get_all()
            default_preprompt = self.preprompt_store.get_default()

        context = {
            "request": request,
            "user": user,
            "is_admin": True,
            "preprompts": preprompts,
            "default_preprompt": default_preprompt,
            "message": message,
            "message_type": message_type,
        }
        return aiohttp_jinja2.render_template("admin/settings.html", request, context)

    async def handle_save_settings(self, request: web.Request) -> web.Response:
        """Handle saving settings."""
        await self._require_admin(request)

        if not self.preprompt_store:
            raise web.HTTPFound("/admin/settings?message=Preprompt+storage+not+configured&type=error")

        data = await request.post()
        action = data.get("action", "save")

        if action == "save":
            name = data.get("name", "").strip()
            content = data.get("content", "").strip()
            is_default = data.get("is_default") == "on"

            if not name:
                raise web.HTTPFound("/admin/settings?message=Name+is+required&type=error")

            self.preprompt_store.create_or_update(name, content, is_default)
            log.info("Preprompt saved", name=name, is_default=is_default)
            raise web.HTTPFound(f"/admin/settings?message=Preprompt+'{name}'+saved&type=success")

        elif action == "delete":
            name = data.get("name", "").strip()
            if name and self.preprompt_store.delete(name):
                log.info("Preprompt deleted", name=name)
                raise web.HTTPFound(f"/admin/settings?message=Preprompt+'{name}'+deleted&type=success")
            raise web.HTTPFound("/admin/settings?message=Preprompt+not+found&type=error")

        elif action == "set_default":
            name = data.get("name", "").strip()
            if name and self.preprompt_store.set_default(name):
                log.info("Preprompt set as default", name=name)
                raise web.HTTPFound(f"/admin/settings?message=Preprompt+'{name}'+set+as+default&type=success")
            raise web.HTTPFound("/admin/settings?message=Preprompt+not+found&type=error")

        raise web.HTTPFound("/admin/settings")

    async def handle_connectors(self, request: web.Request) -> web.Response:
        """Handle the connectors management page."""
        user_data, user = await self._require_admin(request)

        if not self.connector_store:
            raise web.HTTPFound("/admin?message=Connector+store+not+configured&type=error")

        # Get all connectors from store
        pending_connectors = self.connector_store.get_pending()
        approved_connectors = self.connector_store.get_approved()

        # Enhance with connection status from relay server
        pending_with_status = []
        for conn in pending_connectors:
            is_connected = self.relay_server.is_connector_connected(conn.connector_id) if self.relay_server else False
            pending_with_status.append({
                "connector": conn,
                "is_connected": is_connected,
            })

        approved_with_status = []
        for conn in approved_connectors:
            is_connected = self.relay_server.is_connector_connected(conn.connector_id) if self.relay_server else False
            approved_with_status.append({
                "connector": conn,
                "is_connected": is_connected,
            })

        # Get message from query params (for feedback after actions)
        message = request.query.get("message")
        message_type = request.query.get("type", "success")

        context = {
            "request": request,
            "user": user,
            "is_admin": True,
            "pending_connectors": pending_with_status,
            "approved_connectors": approved_with_status,
            "message": message,
            "message_type": message_type,
        }
        return aiohttp_jinja2.render_template("admin/connectors.html", request, context)

    async def handle_approve_connector(self, request: web.Request) -> web.Response:
        """Handle approving a pending connector."""
        await self._require_admin(request)

        if not self.connector_store:
            raise web.HTTPFound("/admin/connectors?message=Connector+store+not+configured&type=error")

        connector_id = request.match_info["connector_id"]

        # Generate API key and approve
        api_key = self.connector_store.approve(connector_id)
        if not api_key:
            raise web.HTTPFound(f"/admin/connectors?message=Connector+{connector_id}+not+found+or+not+pending&type=error")

        # Notify connector if connected
        if self.relay_server:
            await self.relay_server.notify_approval(connector_id, api_key)

        log.info("Connector approved", connector_id=connector_id)
        raise web.HTTPFound(f"/admin/connectors?message=Connector+{connector_id}+approved&type=success")

    async def handle_revoke_connector(self, request: web.Request) -> web.Response:
        """Handle revoking a connector's API key."""
        await self._require_admin(request)

        if not self.connector_store:
            raise web.HTTPFound("/admin/connectors?message=Connector+store+not+configured&type=error")

        connector_id = request.match_info["connector_id"]

        # Revoke the connector
        if not self.connector_store.revoke(connector_id):
            raise web.HTTPFound(f"/admin/connectors?message=Connector+{connector_id}+not+found&type=error")

        # Notify connector if connected
        if self.relay_server:
            await self.relay_server.notify_revoke(connector_id, "API key revoked by admin")

        log.info("Connector revoked", connector_id=connector_id)
        raise web.HTTPFound(f"/admin/connectors?message=Connector+{connector_id}+revoked&type=success")

    async def handle_delete_connector(self, request: web.Request) -> web.Response:
        """Handle deleting a connector entirely."""
        await self._require_admin(request)

        if not self.connector_store:
            raise web.HTTPFound("/admin/connectors?message=Connector+store+not+configured&type=error")

        connector_id = request.match_info["connector_id"]

        # Delete the connector
        if not self.connector_store.delete(connector_id):
            raise web.HTTPFound(f"/admin/connectors?message=Connector+{connector_id}+not+found&type=error")

        # Close connection if still connected
        if self.relay_server:
            await self.relay_server.notify_revoke(connector_id, "Connector deleted by admin")

        log.info("Connector deleted", connector_id=connector_id)
        raise web.HTTPFound(f"/admin/connectors?message=Connector+{connector_id}+deleted&type=success")
