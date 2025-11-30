"""Main entry point for the broker component."""

import asyncio
import base64
import hashlib
import signal
from pathlib import Path

import aiohttp_jinja2
import jinja2
from aiohttp import web
from aiohttp_session import setup as setup_session
from aiohttp_session.cookie_storage import EncryptedCookieStorage
from cryptography.fernet import Fernet

from remotellm.broker.admin import AdminHandler, RequestLogger
from remotellm.broker.api import BrokerAPI
from remotellm.broker.auth import AuthHandler
from remotellm.broker.config import BrokerConfig
from remotellm.broker.connectors import ConnectorStore
from remotellm.broker.health import HealthServer
from remotellm.broker.preprompts import PrepromptStore
from remotellm.broker.router import ModelRouter
from remotellm.broker.test_auth import TestAuthHandler
from remotellm.broker.tunnel_server import TunnelServer
from remotellm.broker.users import UserStore
from remotellm.shared.logging import configure_logging, get_logger

logger = get_logger(__name__)


class Broker:
    """Main broker application."""

    def __init__(self, config: BrokerConfig):
        """Initialize the broker.

        Args:
            config: Broker configuration
        """
        self.config = config

        # Create model router
        self.router = ModelRouter()

        # Build connector config lookup (token → llm_api_key)
        connector_configs: dict[str, str | None] = {}
        for cfg in config.connector_configs:
            connector_configs[cfg.token] = cfg.llm_api_key

        self.tunnel_server = TunnelServer(
            host=config.host,
            port=config.port + 1,  # WebSocket on port+1
            connector_tokens=config.connector_tokens,
            connector_configs=connector_configs,
            auth_timeout=config.auth_timeout,
            ping_interval=config.ping_interval,
            on_connector_registered=self.router.on_connector_registered,
            on_connector_disconnected=self.router.on_connector_disconnected,
        )

        # Create request logger for admin dashboard
        self.request_logger = RequestLogger(max_logs=100)

        self.api = BrokerAPI(
            tunnel_server=self.tunnel_server,
            router=self.router,
            user_api_keys=config.user_api_keys,
            request_timeout=config.request_timeout,
        )
        self.health_server = HealthServer(
            port=config.health_port,
            tunnel_server=self.tunnel_server,
            router=self.router,
        )
        self._shutdown_event = asyncio.Event()
        self._runner: web.AppRunner | None = None

        # Web portal components (initialized if OAuth or test mode enabled)
        self.user_store: UserStore | None = None
        self.preprompt_store: PrepromptStore | None = None
        self.connector_store: ConnectorStore | None = None
        self.auth_handler: AuthHandler | None = None
        self.test_auth_handler: TestAuthHandler | None = None
        self.admin_handler: AdminHandler | None = None

    def _setup_web_portal(self, app: web.Application) -> None:
        """Set up the web portal with OAuth or test mode authentication.

        Args:
            app: The aiohttp application to configure
        """
        if not self.config.portal_enabled:
            logger.info("Web portal not configured (OAuth or test mode required)")
            return

        # Set up Jinja2 templates
        template_dir = Path(__file__).parent / "templates"
        aiohttp_jinja2.setup(
            app,
            loader=jinja2.FileSystemLoader(str(template_dir)),
        )

        # Set up encrypted session storage
        # Derive a proper Fernet key from the session secret using SHA256
        key_hash = hashlib.sha256(self.config.session_secret.encode()).digest()
        fernet_key = base64.urlsafe_b64encode(key_hash)
        fernet = Fernet(fernet_key)
        setup_session(app, EncryptedCookieStorage(fernet))

        # Create user store
        users_file = self.config.users_file or Path("users.yaml")
        self.user_store = UserStore(users_file)

        # Create preprompt store (same directory as users file)
        preprompts_file = users_file.parent / "preprompts.yaml"
        self.preprompt_store = PrepromptStore(preprompts_file)

        # Create connector store (same directory as users file)
        connectors_file = users_file.parent / "connectors.yaml"
        self.connector_store = ConnectorStore(connectors_file)
        # Pass connector_store to tunnel_server for approval workflow
        self.tunnel_server.connector_store = self.connector_store

        # Choose auth handler based on mode
        if self.config.test_mode:
            logger.info("Setting up web portal in TEST MODE")
            # Use test auth handler (simple username form)
            self.test_auth_handler = TestAuthHandler(
                user_store=self.user_store,
                router=self.router,
                public_url=self.config.public_url or "",
            )
            self.test_auth_handler.setup_routes(app)
        else:
            logger.info("Setting up web portal with GitLab OAuth")
            # Use OAuth auth handler
            self.auth_handler = AuthHandler(
                config=self.config,
                user_store=self.user_store,
                router=self.router,
            )
            self.auth_handler.setup_routes(app)

        # Create admin handler
        self.admin_handler = AdminHandler(
            user_store=self.user_store,
            router=self.router,
            request_logger=self.request_logger,
            preprompt_store=self.preprompt_store,
            connector_store=self.connector_store,
            tunnel_server=self.tunnel_server,
        )
        self.admin_handler.setup_routes(app)

        logger.info(
            "Web portal configured", users_file=str(users_file), test_mode=self.config.test_mode
        )

    def _register_mock_connectors(self) -> None:
        """Register mock connectors for test mode."""
        if not self.config.test_mode:
            return

        logger.info("Registering mock connectors for test mode")

        # Register some sample connectors with models
        self.router.on_connector_registered(
            connector_id="mock-openai",
            models=["gpt-4", "gpt-4-turbo", "gpt-3.5-turbo"],
            llm_api_key=None,
        )
        self.router.on_connector_registered(
            connector_id="mock-anthropic",
            models=["claude-3-opus", "claude-3-sonnet", "claude-3-haiku"],
            llm_api_key=None,
        )
        self.router.on_connector_registered(
            connector_id="mock-local",
            models=["llama-3-70b", "mistral-7b"],
            llm_api_key=None,
        )
        # Text-based LLM connectors (for testing remote LLM integration)
        self.router.on_connector_registered(
            connector_id="mock-qwen",
            models=["qwen2.5-coder", "qwen2.5-coder-7b", "qwen3-coder:30b"],
            llm_api_key=None,
        )
        self.router.on_connector_registered(
            connector_id="mock-deepseek",
            models=["deepseek-coder", "deepseek-coder-v2"],
            llm_api_key=None,
        )

        logger.info("Mock connectors registered", connector_count=5, model_count=13)

    async def run(self) -> None:
        """Run the broker."""
        configure_logging(self.config.log_level)
        logger.info(
            "Starting broker",
            host=self.config.host,
            api_port=self.config.port,
            tunnel_port=self.config.port + 1,
        )

        # Set up signal handlers
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))

        # Start tunnel server
        await self.tunnel_server.start()

        # Start health server
        await self.health_server.start()

        # Register mock connectors in test mode
        self._register_mock_connectors()

        # Set up web portal (OAuth or test mode, templates, admin dashboard)
        self._setup_web_portal(self.api.app)

        # Start HTTP API server
        self._runner = web.AppRunner(self.api.app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.config.host, self.config.port)
        await site.start()

        logger.info("Broker ready", api_url=f"http://{self.config.host}:{self.config.port}")

        # Wait for shutdown
        await self._shutdown_event.wait()

    async def shutdown(self) -> None:
        """Initiate graceful shutdown."""
        logger.info("Shutting down broker")
        self._shutdown_event.set()

        # Stop API server
        if self._runner:
            await self._runner.cleanup()

        # Stop health server
        await self.health_server.stop()

        # Stop tunnel server
        await self.tunnel_server.stop()

        logger.info("Broker stopped")


async def run_broker(config: BrokerConfig) -> None:
    """Run the broker with the given configuration.

    Args:
        config: Broker configuration
    """
    broker = Broker(config)
    await broker.run()
