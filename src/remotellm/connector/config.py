"""Configuration for the connector component."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConnectorConfig(BaseSettings):
    """Configuration for the LLM connector."""

    model_config = SettingsConfigDict(
        env_prefix="REMOTELLM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Local LLM connection
    llm_url: str = Field(
        default="http://localhost:11434",
        description="URL of the local LLM server (OpenAI-compatible API)",
    )
    llm_api_key: str | None = Field(
        default=None,
        description="API key for the LLM server (for authenticated endpoints)",
    )
    llm_host: str | None = Field(
        default=None,
        description="Custom Host header for LLM requests (for servers behind reverse proxies)",
    )

    # Models served by this connector (T028)
    models: list[str] = Field(
        default_factory=list,
        description="List of model names served by this connector",
    )

    # Broker connection
    broker_url: str = Field(
        description="WebSocket URL of the broker to connect to",
    )
    broker_token: str = Field(
        description="Authentication token for broker connection",
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

    # SSL verification
    llm_ssl_verify: bool = Field(
        default=True,
        description="Verify SSL certificates when connecting to LLM server",
    )

    # Timeouts (seconds)
    llm_timeout: float = Field(
        default=300.0,
        description="Timeout for LLM requests",
    )
    connect_timeout: float = Field(
        default=30.0,
        description="Timeout for initial connection",
    )

    # Reconnection
    reconnect_base_delay: float = Field(
        default=1.0,
        description="Base delay for exponential backoff reconnection",
    )
    reconnect_max_delay: float = Field(
        default=300.0,
        description="Maximum delay between reconnection attempts (seconds)",
    )
