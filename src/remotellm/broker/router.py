"""Model router for multi-connector request routing."""

from dataclasses import dataclass

from remotellm.shared.logging import get_logger

logger = get_logger(__name__)


@dataclass
class RouteInfo:
    """Information about a route to a connector."""

    connector_id: str
    llm_api_key: str | None


@dataclass
class ConnectorInfo:
    """Information about a registered connector."""

    connector_id: str
    models: list[str]
    llm_api_key: str | None


class ModelRouter:
    """Routes requests to connectors based on model name.

    Maintains a mapping of model names to connector IDs and their
    associated LLM API keys. Updates automatically when connectors
    register or disconnect.
    """

    def __init__(self) -> None:
        """Initialize the model router."""
        # model_name → RouteInfo
        self._routes: dict[str, RouteInfo] = {}
        # connector_id → ConnectorInfo
        self._connectors: dict[str, ConnectorInfo] = {}

    @property
    def available_models(self) -> list[str]:
        """Get list of all available models."""
        return list(self._routes.keys())

    @property
    def connector_count(self) -> int:
        """Get number of registered connectors."""
        return len(self._connectors)

    def build_routes(self) -> None:
        """Rebuild the model→connector routing table from registered connectors.

        Called after connector registration/disconnection to update routes.
        """
        self._routes.clear()

        for connector_id, info in self._connectors.items():
            for model in info.models:
                if model not in self._routes:
                    self._routes[model] = RouteInfo(
                        connector_id=connector_id,
                        llm_api_key=info.llm_api_key,
                    )
                    logger.debug(
                        "Route added",
                        model=model,
                        connector_id=connector_id,
                    )
                else:
                    # Model already has a route, log but don't override
                    # (first connector wins for now)
                    logger.debug(
                        "Model already routed, skipping",
                        model=model,
                        existing_connector=self._routes[model].connector_id,
                        new_connector=connector_id,
                    )

        logger.info(
            "Routes rebuilt",
            model_count=len(self._routes),
            connector_count=len(self._connectors),
        )

    def get_route(self, model: str) -> tuple[str, str | None] | None:
        """Get the route for a model.

        Args:
            model: The model name to route

        Returns:
            Tuple of (connector_id, llm_api_key) if route exists, None otherwise
        """
        route = self._routes.get(model)
        if route is None:
            return None
        return (route.connector_id, route.llm_api_key)

    def on_connector_registered(
        self,
        connector_id: str,
        models: list[str],
        llm_api_key: str | None,
    ) -> None:
        """Handle connector registration.

        Args:
            connector_id: The connector's unique ID
            models: List of models served by this connector
            llm_api_key: The LLM API key for this connector (from broker config)
        """
        self._connectors[connector_id] = ConnectorInfo(
            connector_id=connector_id,
            models=models,
            llm_api_key=llm_api_key,
        )
        logger.info(
            "Connector registered with router",
            connector_id=connector_id,
            models=models,
            has_llm_api_key=llm_api_key is not None,
        )
        self.build_routes()

    def on_connector_disconnected(self, connector_id: str) -> None:
        """Handle connector disconnection.

        Args:
            connector_id: The connector's unique ID
        """
        if connector_id in self._connectors:
            info = self._connectors.pop(connector_id)
            logger.info(
                "Connector removed from router",
                connector_id=connector_id,
                models=info.models,
            )
            self.build_routes()
        else:
            logger.warning(
                "Connector not found in router",
                connector_id=connector_id,
            )

    def get_connector_models(self, connector_id: str) -> list[str] | None:
        """Get the models served by a specific connector.

        Args:
            connector_id: The connector's unique ID

        Returns:
            List of model names if connector exists, None otherwise
        """
        info = self._connectors.get(connector_id)
        if info is None:
            return None
        return info.models

    def get_all_models_with_connectors(self) -> dict[str, str]:
        """Get a mapping of all models to their connector IDs.

        Returns:
            Dict mapping model name to connector ID
        """
        return {model: route.connector_id for model, route in self._routes.items()}

    def get_connector_info(self) -> list[dict]:
        """Get info about all connectors for display (no sensitive data).

        Returns:
            List of connector info dicts with id, models, and connected status
        """
        return [
            {
                "id": info.connector_id,
                "models": info.models,
                "model_count": len(info.models),
                "connected": True,  # If it's in the dict, it's connected
            }
            for info in self._connectors.values()
        ]
