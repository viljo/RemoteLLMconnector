"""CLI entry point for the broker component."""

import asyncio
from pathlib import Path

import click
import yaml

from remotellm.broker.config import BrokerConfig, ConnectorConfigEntry
from remotellm.broker.main import run_broker


def load_connector_configs(config_file: Path | None) -> list[ConnectorConfigEntry]:
    """Load connector configurations from YAML file."""
    if config_file is None or not config_file.exists():
        return []

    with open(config_file) as f:
        data = yaml.safe_load(f)

    configs = []
    for entry in data.get("connectors", []):
        configs.append(
            ConnectorConfigEntry(
                token=entry["token"],
                llm_api_key=entry.get("llm_api_key"),
            )
        )
    return configs


@click.command()
@click.option(
    "--host",
    envvar="REMOTELLM_BROKER_HOST",
    default="0.0.0.0",
    help="Host to bind the server to",
)
@click.option(
    "--port",
    envvar="REMOTELLM_BROKER_PORT",
    default=8443,
    type=int,
    help="Port for the HTTP API (WebSocket relay at /ws path)",
)
@click.option(
    "--connector-token",
    "connector_tokens",
    envvar="REMOTELLM_BROKER_CONNECTOR_TOKENS",
    multiple=True,
    help="Valid tokens for connector authentication (can be specified multiple times)",
)
@click.option(
    "--user-api-key",
    "user_api_keys",
    envvar="REMOTELLM_BROKER_USER_API_KEYS",
    multiple=True,
    help="Valid API keys for external user authentication (can be specified multiple times)",
)
@click.option(
    "--connector-config",
    "connector_config_file",
    envvar="REMOTELLM_BROKER_CONNECTOR_CONFIG",
    type=click.Path(exists=False, path_type=Path),
    default=None,
    help="Path to YAML file with connector configurations (token → llm_api_key mapping)",
)
@click.option(
    "--health-port",
    envvar="REMOTELLM_BROKER_HEALTH_PORT",
    default=8080,
    type=int,
    help="Port for health check endpoint",
)
@click.option(
    "--log-level",
    envvar="REMOTELLM_BROKER_LOG_LEVEL",
    default="INFO",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
    help="Logging level",
)
# OAuth options for web portal
@click.option(
    "--gitlab-url",
    envvar="REMOTELLM_BROKER_GITLAB_URL",
    default=None,
    help="GitLab instance URL (e.g., https://gitlab.com)",
)
@click.option(
    "--gitlab-client-id",
    envvar="REMOTELLM_BROKER_GITLAB_CLIENT_ID",
    default=None,
    help="GitLab OAuth application client ID",
)
@click.option(
    "--gitlab-client-secret",
    envvar="REMOTELLM_BROKER_GITLAB_CLIENT_SECRET",
    default=None,
    help="GitLab OAuth application client secret",
)
@click.option(
    "--gitlab-redirect-uri",
    envvar="REMOTELLM_BROKER_GITLAB_REDIRECT_URI",
    default=None,
    help="GitLab OAuth redirect URI",
)
@click.option(
    "--public-url",
    envvar="REMOTELLM_BROKER_PUBLIC_URL",
    default=None,
    help="Public URL of the broker for display in portal",
)
@click.option(
    "--session-secret",
    envvar="REMOTELLM_BROKER_SESSION_SECRET",
    default=None,
    help="Secret key for session encryption (32+ chars)",
)
@click.option(
    "--users-file",
    envvar="REMOTELLM_BROKER_USERS_FILE",
    type=click.Path(exists=False, path_type=Path),
    default=None,
    help="Path to YAML file for user storage",
)
@click.option(
    "--test-mode",
    "test_mode",
    envvar="REMOTELLM_BROKER_TEST_MODE",
    is_flag=True,
    default=False,
    help="Enable interactive test mode (bypasses OAuth, adds sample data)",
)
def main(
    host: str,
    port: int,
    connector_tokens: tuple[str, ...],
    user_api_keys: tuple[str, ...],
    connector_config_file: Path | None,
    health_port: int,
    log_level: str,
    gitlab_url: str | None,
    gitlab_client_id: str | None,
    gitlab_client_secret: str | None,
    gitlab_redirect_uri: str | None,
    public_url: str | None,
    session_secret: str | None,
    users_file: Path | None,
    test_mode: bool,
) -> None:
    """Run the RemoteLLM broker.

    The broker accepts connections from connectors and exposes an
    OpenAI-compatible API for external users.
    """
    # Load connector configs from YAML file if provided
    connector_configs = load_connector_configs(connector_config_file)

    # Merge connector tokens from CLI and config file
    all_connector_tokens = list(connector_tokens)
    for cfg in connector_configs:
        if cfg.token not in all_connector_tokens:
            all_connector_tokens.append(cfg.token)

    # In test mode, provide defaults for session and public URL
    if test_mode:
        if session_secret is None:
            session_secret = "test-session-secret-for-development-only"
        if public_url is None:
            public_url = f"http://localhost:{port}"

    config = BrokerConfig(
        host=host,
        port=port,
        connector_tokens=all_connector_tokens,
        user_api_keys=list(user_api_keys),
        connector_configs=connector_configs,
        connector_config_file=connector_config_file,
        health_port=health_port,
        log_level=log_level.upper(),
        # OAuth settings
        gitlab_url=gitlab_url,
        gitlab_client_id=gitlab_client_id,
        gitlab_client_secret=gitlab_client_secret,
        gitlab_redirect_uri=gitlab_redirect_uri,
        public_url=public_url,
        session_secret=session_secret,
        users_file=users_file,
        test_mode=test_mode,
    )

    asyncio.run(run_broker(config))


if __name__ == "__main__":
    main()
