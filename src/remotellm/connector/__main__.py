"""CLI entry point for the connector component."""

import asyncio

import click

from remotellm.connector.config import ConnectorConfig
from remotellm.connector.main import run_connector


@click.command()
@click.option(
    "--llm-url",
    envvar="REMOTELLM_LLM_URL",
    default="http://localhost:11434",
    help="URL of the local LLM server",
)
@click.option(
    "--llm-api-key",
    envvar="REMOTELLM_LLM_API_KEY",
    default=None,
    help="API key for the LLM server (for authenticated endpoints)",
)
@click.option(
    "--llm-host",
    envvar="REMOTELLM_LLM_HOST",
    default=None,
    help="Custom Host header for LLM requests (for servers behind reverse proxies)",
)
@click.option(
    "--no-ssl-verify",
    "llm_ssl_verify",
    envvar="REMOTELLM_LLM_SSL_VERIFY",
    is_flag=True,
    flag_value=False,
    default=True,
    help="Disable SSL certificate verification for LLM server",
)
@click.option(
    "--model",
    "models",
    envvar="REMOTELLM_MODELS",
    multiple=True,
    help="Override model names (optional, auto-discovered from LLM if not specified)",
)
@click.option(
    "--broker-url",
    envvar="REMOTELLM_BROKER_URL",
    required=True,
    help="WebSocket URL of the broker",
)
@click.option(
    "--broker-token",
    envvar="REMOTELLM_BROKER_TOKEN",
    required=True,
    help="Authentication token for the broker",
)
@click.option(
    "--health-port",
    envvar="REMOTELLM_HEALTH_PORT",
    default=8080,
    type=int,
    help="Port for health check endpoint",
)
@click.option(
    "--log-level",
    envvar="REMOTELLM_LOG_LEVEL",
    default="INFO",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
    help="Logging level",
)
def main(
    llm_url: str,
    llm_api_key: str | None,
    llm_host: str | None,
    llm_ssl_verify: bool,
    models: tuple[str, ...],
    broker_url: str,
    broker_token: str,
    health_port: int,
    log_level: str,
) -> None:
    """Run the RemoteLLM connector.

    The connector bridges your local LLM server to the external broker,
    allowing external users to access your LLM through the API.
    """
    config = ConnectorConfig(
        llm_url=llm_url,
        llm_api_key=llm_api_key,
        llm_host=llm_host,
        llm_ssl_verify=llm_ssl_verify,
        models=list(models),
        broker_url=broker_url,
        broker_token=broker_token,
        health_port=health_port,
        log_level=log_level.upper(),
    )

    asyncio.run(run_connector(config))


if __name__ == "__main__":
    main()
