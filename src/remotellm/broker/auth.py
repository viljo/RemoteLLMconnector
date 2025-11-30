"""GitLab OAuth authentication for the broker web portal."""

from __future__ import annotations

from typing import TYPE_CHECKING

import aiohttp_jinja2
import structlog
from aiohttp import web
from aiohttp_session import get_session
from authlib.integrations.httpx_client import AsyncOAuth2Client

from .config import BrokerConfig
from .users import UserRole, UserStore

if TYPE_CHECKING:
    from .router import ModelRouter

log = structlog.get_logger()


class AuthHandler:
    """Handler for GitLab OAuth authentication."""

    def __init__(
        self,
        config: BrokerConfig,
        user_store: UserStore,
        router: ModelRouter,
    ) -> None:
        """Initialize the auth handler.

        Args:
            config: Broker configuration with OAuth settings
            user_store: User storage instance
            router: Model router for getting available models
        """
        self.gitlab_url = config.gitlab_url.rstrip("/")
        self.client_id = config.gitlab_client_id
        self.client_secret = config.gitlab_client_secret
        self.redirect_uri = config.gitlab_redirect_uri
        self.user_store = user_store
        self.router = router
        self.public_url = (config.public_url or "").rstrip("/")
        self.connector_tokens = config.connector_tokens

        # OAuth endpoints
        self.authorize_url = f"{self.gitlab_url}/oauth/authorize"
        self.token_url = f"{self.gitlab_url}/oauth/token"
        self.userinfo_url = f"{self.gitlab_url}/api/v4/user"

    def _create_oauth_client(self) -> AsyncOAuth2Client:
        """Create a new OAuth client instance."""
        return AsyncOAuth2Client(
            client_id=self.client_id,
            client_secret=self.client_secret,
            redirect_uri=self.redirect_uri,
        )

    def setup_routes(self, app: web.Application) -> None:
        """Register auth routes with the application."""
        app.router.add_get("/", self.handle_index)
        app.router.add_get("/auth/login", self.handle_login)
        app.router.add_get("/auth/callback", self.handle_callback)
        app.router.add_get("/auth/logout", self.handle_logout)
        app.router.add_get("/dashboard", self.handle_dashboard)
        app.router.add_get("/services", self.handle_services)
        app.router.add_get("/chat", self.handle_chat)
        app.router.add_get("/connect", self.handle_connect)

    async def handle_index(self, request: web.Request) -> web.Response:
        """Handle the index page - redirect to dashboard or login."""
        session = await get_session(request)
        if "user" in session:
            raise web.HTTPFound("/dashboard")
        raise web.HTTPFound("/auth/login")

    async def handle_login(self, request: web.Request) -> web.Response:
        """Handle the login page."""
        session = await get_session(request)

        # If already logged in, redirect to dashboard
        if "user" in session:
            raise web.HTTPFound("/dashboard")

        # Create OAuth client and generate authorization URL
        client = self._create_oauth_client()
        uri, state = client.create_authorization_url(
            self.authorize_url,
            scope="read_user",
        )

        # Store state in session for CSRF protection
        session["oauth_state"] = state

        context = {
            "request": request,
            "gitlab_url": self.gitlab_url,
            "authorize_url": uri,
        }
        return aiohttp_jinja2.render_template("login.html", request, context)

    async def handle_callback(self, request: web.Request) -> web.Response:
        """Handle the OAuth callback from GitLab."""
        session = await get_session(request)

        # Verify state for CSRF protection
        state = request.query.get("state")
        expected_state = session.pop("oauth_state", None)
        if not state or state != expected_state:
            log.warning("OAuth state mismatch", received=state, expected=expected_state)
            raise web.HTTPBadRequest(text="Invalid OAuth state")

        # Check for error from GitLab
        error = request.query.get("error")
        if error:
            error_desc = request.query.get("error_description", "Unknown error")
            log.warning("OAuth error from GitLab", error=error, description=error_desc)
            raise web.HTTPBadRequest(text=f"GitLab OAuth error: {error_desc}")

        # Exchange code for token
        code = request.query.get("code")
        if not code:
            raise web.HTTPBadRequest(text="Missing authorization code")

        try:
            client = self._create_oauth_client()
            token = await client.fetch_token(
                self.token_url,
                code=code,
                grant_type="authorization_code",
            )

            # Fetch user info from GitLab
            client.token = token
            resp = await client.get(self.userinfo_url)
            userinfo = resp.json()

            gitlab_username = userinfo.get("username")
            gitlab_id = userinfo.get("id")

            if not gitlab_username or not gitlab_id:
                raise web.HTTPBadRequest(text="Invalid user info from GitLab")

            log.info("GitLab OAuth successful", username=gitlab_username, id=gitlab_id)

            # Get or create user
            user = self.user_store.get_by_username(gitlab_username)
            if user is None:
                user = self.user_store.create_user(gitlab_username, gitlab_id)
                log.info(
                    "Created new user via OAuth", username=gitlab_username, role=user.role.value
                )
            else:
                self.user_store.update_last_used(user)

            # Check if blocked
            if user.blocked:
                log.warning("Blocked user attempted login", username=gitlab_username)
                raise web.HTTPForbidden(text="Your account has been blocked")

            # Store user in session
            session["user"] = {
                "username": user.gitlab_username,
                "role": user.role.value,
                "api_key": user.api_key,
            }

            raise web.HTTPFound("/dashboard")

        except web.HTTPException:
            raise
        except Exception as e:
            log.error("OAuth callback error", error=str(e))
            raise web.HTTPInternalServerError(text="Authentication failed") from None

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
        }
        return aiohttp_jinja2.render_template("services.html", request, context)

    async def handle_chat(self, request: web.Request) -> web.Response:
        """Handle the chat page."""
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
            "test_mode": False,
        }
        return aiohttp_jinja2.render_template("chat.html", request, context)

    async def handle_connect(self, request: web.Request) -> web.Response:
        """Handle the connect page (instructions for connecting LLMs)."""
        session = await get_session(request)
        user_data = session.get("user")

        if not user_data:
            raise web.HTTPFound("/auth/login")

        # Get user from store
        user = self.user_store.get_by_username(user_data["username"])
        if user is None or user.blocked:
            session.clear()
            raise web.HTTPFound("/auth/login")

        # Only admins can see connector tokens
        if user.role != UserRole.ADMIN:
            raise web.HTTPForbidden(text="Admin access required")

        # Build WebSocket URL from public URL
        ws_url = self.public_url.replace("https://", "wss://").replace("http://", "ws://")
        ws_url = f"{ws_url}/ws"

        # Get first connector token (or placeholder)
        connector_token = self.connector_tokens[0] if self.connector_tokens else "request-token-from-admin"

        context = {
            "request": request,
            "user": user,
            "ws_url": ws_url,
            "connector_token": connector_token,
            "is_admin": user.role == UserRole.ADMIN,
        }
        return aiohttp_jinja2.render_template("connect.html", request, context)


def require_auth(handler):
    """Decorator to require authentication for a handler."""

    async def wrapper(request: web.Request) -> web.Response:
        session = await get_session(request)
        if "user" not in session:
            raise web.HTTPFound("/auth/login")
        return await handler(request)

    return wrapper


def require_admin(handler):
    """Decorator to require admin role for a handler."""

    async def wrapper(request: web.Request) -> web.Response:
        session = await get_session(request)
        user_data = session.get("user")
        if not user_data:
            raise web.HTTPFound("/auth/login")
        if user_data.get("role") != "admin":
            raise web.HTTPForbidden(text="Admin access required")
        return await handler(request)

    return wrapper
