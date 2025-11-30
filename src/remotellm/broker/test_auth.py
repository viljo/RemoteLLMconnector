"""Test mode authentication handler for interactive testing without OAuth."""

from __future__ import annotations

from typing import TYPE_CHECKING

import aiohttp_jinja2
import structlog
from aiohttp import web
from aiohttp_session import get_session

from .users import UserRole, UserStore

if TYPE_CHECKING:
    from .router import ModelRouter

log = structlog.get_logger()


class TestAuthHandler:
    """Handler for test mode authentication (bypasses OAuth).

    This handler provides a simple username form for testing the web portal
    without requiring a GitLab OAuth setup.
    """

    def __init__(
        self,
        user_store: UserStore,
        router: ModelRouter,
        public_url: str = "",
    ) -> None:
        """Initialize the test auth handler.

        Args:
            user_store: User storage instance
            router: Model router for getting available models
            public_url: Public URL of the broker
        """
        self.user_store = user_store
        self.router = router
        self.public_url = public_url.rstrip("/")

    def setup_routes(self, app: web.Application) -> None:
        """Register auth routes with the application."""
        app.router.add_get("/", self.handle_index)
        app.router.add_get("/auth/login", self.handle_login)
        app.router.add_post("/auth/login", self.handle_login_submit)
        app.router.add_get("/auth/logout", self.handle_logout)
        app.router.add_get("/dashboard", self.handle_dashboard)
        app.router.add_get("/services", self.handle_services)
        app.router.add_get("/chat", self.handle_chat)

    async def handle_index(self, request: web.Request) -> web.Response:
        """Handle the index page - redirect to chat or login."""
        session = await get_session(request)
        if "user" in session:
            raise web.HTTPFound("/chat")
        raise web.HTTPFound("/auth/login")

    async def handle_login(self, request: web.Request) -> web.Response:
        """Handle the login page."""
        session = await get_session(request)

        # If already logged in, redirect to chat
        if "user" in session:
            raise web.HTTPFound("/chat")

        context = {
            "error": None,
            "test_mode": True,
        }
        return aiohttp_jinja2.render_template("test_login.html", request, context)

    async def handle_login_submit(self, request: web.Request) -> web.Response:
        """Handle the login form submission."""
        data = await request.post()
        username = data.get("username", "").strip()

        if not username:
            context = {"error": "Username is required", "test_mode": True}
            return aiohttp_jinja2.render_template("test_login.html", request, context)

        # Validate username format (alphanumeric and underscores only)
        if not username.replace("_", "").replace("-", "").isalnum():
            context = {"error": "Invalid username format", "test_mode": True}
            return aiohttp_jinja2.render_template("test_login.html", request, context)

        log.info("Test mode login", username=username)

        # Get or create user
        user = self.user_store.get_by_username(username)
        if user is None:
            # Generate a fake GitLab ID based on username hash
            gitlab_id = abs(hash(username)) % 1000000
            user = self.user_store.create_user(username, gitlab_id)
            log.info("Created new user in test mode", username=username, role=user.role.value)
        else:
            self.user_store.update_last_used(user)

        # Check if blocked
        if user.blocked:
            context = {"error": "Your account has been blocked", "test_mode": True}
            return aiohttp_jinja2.render_template("test_login.html", request, context)

        # Store user in session
        session = await get_session(request)
        session["user"] = {
            "username": user.gitlab_username,
            "role": user.role.value,
            "api_key": user.api_key,
        }

        raise web.HTTPFound("/chat")

    async def handle_logout(self, request: web.Request) -> web.Response:
        """Handle logout."""
        session = await get_session(request)
        session.clear()
        raise web.HTTPFound("/auth/login")

    async def handle_dashboard(self, request: web.Request) -> web.Response:
        """Handle the user dashboard page."""
        session = await get_session(request)
        user_data = session.get("user")

        if not user_data:
            raise web.HTTPFound("/auth/login")

        # Get user from store to ensure we have fresh data
        user = self.user_store.get_by_username(user_data["username"])
        if user is None or user.blocked:
            session.clear()
            raise web.HTTPFound("/auth/login")

        # Get available models from router
        models = self.router.available_models

        context = {
            "request": request,
            "user": user,
            "api_key": user.api_key,
            "api_url": f"{self.public_url}/v1",
            "models": models,
            "is_admin": user.role == UserRole.ADMIN,
            "test_mode": True,
        }
        return aiohttp_jinja2.render_template("dashboard.html", request, context)

    async def handle_services(self, request: web.Request) -> web.Response:
        """Handle the connected services page."""
        session = await get_session(request)
        user_data = session.get("user")

        if not user_data:
            raise web.HTTPFound("/auth/login")

        # Get user from store
        user = self.user_store.get_by_username(user_data["username"])
        if user is None or user.blocked:
            session.clear()
            raise web.HTTPFound("/auth/login")

        # Get connector info from router
        connectors = self.router.get_connector_info()

        context = {
            "request": request,
            "user": user,
            "connectors": connectors,
            "is_admin": user.role == UserRole.ADMIN,
            "test_mode": True,
        }
        return aiohttp_jinja2.render_template("services.html", request, context)

    async def handle_chat(self, request: web.Request) -> web.Response:
        """Handle the chat/test page."""
        session = await get_session(request)
        user_data = session.get("user")

        if not user_data:
            raise web.HTTPFound("/auth/login")

        # Get user from store
        user = self.user_store.get_by_username(user_data["username"])
        if user is None or user.blocked:
            session.clear()
            raise web.HTTPFound("/auth/login")

        # Get available models from router with connector info
        models = self.router.available_models
        model_connectors = self.router.get_all_models_with_connectors()

        context = {
            "request": request,
            "user": user,
            "api_key": user.api_key,
            "models": models,
            "model_connectors": model_connectors,
            "is_admin": user.role == UserRole.ADMIN,
            "test_mode": True,
        }
        return aiohttp_jinja2.render_template("chat.html", request, context)
