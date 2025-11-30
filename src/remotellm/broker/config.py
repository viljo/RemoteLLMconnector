"""Configuration for the broker component."""

from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConnectorConfigEntry(BaseModel):
    """Configuration entry for a connector (token → llm_api_key mapping)."""

    token: str = Field(description="Connector authentication token")
    llm_api_key: str | None = Field(default=None, description="LLM API key for this connector")


class BrokerConfig(BaseSettings):
    """Configuration for the LLM broker."""

    model_config = SettingsConfigDict(
        env_prefix="REMOTELLM_BROKER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Server binding
    host: str = Field(
        default="0.0.0.0",
        description="Host to bind the server to",
    )
    port: int = Field(
        default=8443,
        description="Port for HTTPS/WSS server",
    )

    # Tunnel authentication
    connector_tokens: list[str] = Field(
        default_factory=list,
        description="Valid tokens for connector authentication",
    )

    # External user authentication
    user_api_keys: list[str] = Field(
        default_factory=list,
        description="Valid API keys for external user authentication",
    )

    # Connector configs (token → llm_api_key mapping) loaded from YAML file
    connector_configs: list[ConnectorConfigEntry] = Field(
        default_factory=list,
        description="Connector configurations with LLM API keys",
    )

    # Path to connector configs YAML file
    connector_config_file: Path | None = Field(
        default=None,
        description="Path to YAML file with connector configurations",
    )

    # Health endpoint
    health_port: int = Field(
        default=8080,
        description="Port for the health check endpoint",
    )

    # Logging
    log_level: str = Field(
        default="INFO",
        description="Logging level (DEBUG, INFO, WARNING, ERROR)",
    )

    # Timeouts (seconds)
    auth_timeout: float = Field(
        default=10.0,
        description="Timeout for connector authentication",
    )
    request_timeout: float = Field(
        default=300.0,
        description="Timeout for request processing",
    )
    ping_interval: float = Field(
        default=30.0,
        description="Interval for ping/pong health checks",
    )

    # GitLab OAuth (optional - web portal requires these)
    gitlab_url: str | None = Field(
        default=None,
        description="GitLab instance URL (e.g., https://gitlab.com)",
    )
    gitlab_client_id: str | None = Field(
        default=None,
        description="GitLab OAuth application client ID",
    )
    gitlab_client_secret: str | None = Field(
        default=None,
        description="GitLab OAuth application client secret",
    )
    gitlab_redirect_uri: str | None = Field(
        default=None,
        description="GitLab OAuth redirect URI",
    )

    # Web portal settings
    public_url: str | None = Field(
        default=None,
        description="Public URL of the broker for display in portal",
    )
    session_secret: str | None = Field(
        default=None,
        description="Secret key for session encryption (32+ hex chars)",
    )
    users_file: Path | None = Field(
        default=None,
        description="Path to YAML file for user storage",
    )

    # Test mode (for interactive testing without OAuth)
    test_mode: bool = Field(
        default=False,
        description="Enable test mode with mock auth and sample data",
    )

    @property
    def oauth_enabled(self) -> bool:
        """Check if OAuth is configured."""
        return all(
            [
                self.gitlab_url,
                self.gitlab_client_id,
                self.gitlab_client_secret,
                self.gitlab_redirect_uri,
                self.session_secret,
            ]
        )

    @property
    def portal_enabled(self) -> bool:
        """Check if the web portal should be enabled (OAuth or test mode)."""
        return self.oauth_enabled or self.test_mode
