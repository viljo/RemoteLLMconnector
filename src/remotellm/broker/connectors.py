"""Connector model and storage for the broker approval workflow."""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

import structlog
import yaml

log = structlog.get_logger()


class ConnectorStatus(str, Enum):
    """Status of a connector in the approval workflow."""

    PENDING = "pending"
    APPROVED = "approved"
    REVOKED = "revoked"


@dataclass
class Connector:
    """A registered connector with its API key and status."""

    connector_id: str
    api_key: str | None  # None for pending connectors
    name: str | None = None
    models: list[str] = field(default_factory=list)
    status: ConnectorStatus = ConnectorStatus.PENDING
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_used: datetime | None = None
    last_connected: datetime | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary for YAML serialization."""
        return {
            "connector_id": self.connector_id,
            "api_key": self.api_key,
            "name": self.name,
            "models": self.models,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "last_used": self.last_used.isoformat() if self.last_used else None,
            "last_connected": self.last_connected.isoformat() if self.last_connected else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Connector:
        """Create Connector from dictionary."""
        return cls(
            connector_id=data["connector_id"],
            api_key=data.get("api_key"),
            name=data.get("name"),
            models=data.get("models", []),
            status=ConnectorStatus(data.get("status", "pending")),
            created_at=datetime.fromisoformat(data["created_at"]),
            last_used=datetime.fromisoformat(data["last_used"]) if data.get("last_used") else None,
            last_connected=datetime.fromisoformat(data["last_connected"]) if data.get("last_connected") else None,
        )


class ConnectorStore:
    """YAML-based connector storage with in-memory caching."""

    def __init__(self, file_path: Path | str | None = None) -> None:
        """Initialize the connector store.

        Args:
            file_path: Path to the connectors.yaml file. If None, operates in memory-only mode.
        """
        self.file_path = Path(file_path) if file_path else None
        self._connectors: dict[str, Connector] = {}  # Keyed by connector_id
        self._api_key_index: dict[str, Connector] = {}  # Keyed by api_key
        self._load()

    def _load(self) -> None:
        """Load connectors from YAML file."""
        if self.file_path is None or not self.file_path.exists():
            log.info("No connectors file found, starting with empty connector store")
            return

        try:
            with open(self.file_path) as f:
                data = yaml.safe_load(f)

            if data and "connectors" in data:
                for connector_data in data["connectors"]:
                    connector = Connector.from_dict(connector_data)
                    self._connectors[connector.connector_id] = connector
                    if connector.api_key:
                        self._api_key_index[connector.api_key] = connector

            log.info("Loaded connectors from file", count=len(self._connectors), file=str(self.file_path))
        except Exception as e:
            log.error("Failed to load connectors file", error=str(e), file=str(self.file_path))

    def _save(self) -> None:
        """Save connectors to YAML file."""
        if self.file_path is None:
            return

        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            data = {"connectors": [connector.to_dict() for connector in self._connectors.values()]}
            with open(self.file_path, "w") as f:
                yaml.dump(data, f, default_flow_style=False, sort_keys=False)
            log.debug("Saved connectors to file", count=len(self._connectors))
        except Exception as e:
            log.error("Failed to save connectors file", error=str(e))

    def get_by_id(self, connector_id: str) -> Connector | None:
        """Get connector by ID."""
        return self._connectors.get(connector_id)

    def get_by_api_key(self, api_key: str) -> Connector | None:
        """Get connector by API key."""
        return self._api_key_index.get(api_key)

    def get_all(self) -> list[Connector]:
        """Get all connectors."""
        return list(self._connectors.values())

    def get_pending(self) -> list[Connector]:
        """Get all pending connectors awaiting approval."""
        return [c for c in self._connectors.values() if c.status == ConnectorStatus.PENDING]

    def get_approved(self) -> list[Connector]:
        """Get all approved connectors."""
        return [c for c in self._connectors.values() if c.status == ConnectorStatus.APPROVED]

    def get_revoked(self) -> list[Connector]:
        """Get all revoked connectors."""
        return [c for c in self._connectors.values() if c.status == ConnectorStatus.REVOKED]

    def create_pending(
        self,
        models: list[str] | None = None,
        name: str | None = None,
    ) -> Connector:
        """Create a new pending connector awaiting approval.

        Args:
            models: List of models this connector serves
            name: Optional friendly name for the connector

        Returns:
            The created Connector in pending status
        """
        connector_id = f"conn-{uuid.uuid4().hex[:8]}"

        connector = Connector(
            connector_id=connector_id,
            api_key=None,  # No API key until approved
            name=name,
            models=models or [],
            status=ConnectorStatus.PENDING,
            last_connected=datetime.utcnow(),
        )

        self._connectors[connector_id] = connector
        self._save()

        log.info(
            "Created pending connector",
            connector_id=connector_id,
            name=name,
            models=models,
        )
        return connector

    def approve(self, connector_id: str) -> str | None:
        """Approve a pending connector and generate an API key.

        Args:
            connector_id: The connector ID to approve

        Returns:
            The generated API key, or None if connector not found
        """
        connector = self._connectors.get(connector_id)
        if connector is None:
            return None

        if connector.status != ConnectorStatus.PENDING:
            log.warning("Connector not in pending status", connector_id=connector_id, status=connector.status.value)
            return None

        # Generate API key
        api_key = f"ck-{secrets.token_hex(16)}"
        connector.api_key = api_key
        connector.status = ConnectorStatus.APPROVED

        self._api_key_index[api_key] = connector
        self._save()

        log.info("Approved connector", connector_id=connector_id)
        return api_key

    def revoke(self, connector_id: str, reason: str | None = None) -> bool:
        """Revoke a connector's API key.

        Args:
            connector_id: The connector ID to revoke
            reason: Optional reason for revocation

        Returns:
            True if connector was revoked, False if not found
        """
        connector = self._connectors.get(connector_id)
        if connector is None:
            return False

        # Remove from API key index
        if connector.api_key and connector.api_key in self._api_key_index:
            del self._api_key_index[connector.api_key]

        connector.status = ConnectorStatus.REVOKED
        self._save()

        log.info("Revoked connector", connector_id=connector_id, reason=reason)
        return True

    def delete(self, connector_id: str) -> bool:
        """Delete a connector entirely.

        Args:
            connector_id: The connector ID to delete

        Returns:
            True if connector was deleted, False if not found
        """
        connector = self._connectors.get(connector_id)
        if connector is None:
            return False

        # Remove from API key index
        if connector.api_key and connector.api_key in self._api_key_index:
            del self._api_key_index[connector.api_key]

        del self._connectors[connector_id]
        self._save()

        log.info("Deleted connector", connector_id=connector_id)
        return True

    def update_last_used(self, connector: Connector) -> None:
        """Update the last_used timestamp for a connector."""
        connector.last_used = datetime.utcnow()
        self._save()

    def update_last_connected(self, connector: Connector) -> None:
        """Update the last_connected timestamp for a connector."""
        connector.last_connected = datetime.utcnow()
        self._save()

    def update_models(self, connector_id: str, models: list[str]) -> bool:
        """Update the models list for a connector.

        Args:
            connector_id: The connector ID to update
            models: New list of models

        Returns:
            True if connector was updated, False if not found
        """
        connector = self._connectors.get(connector_id)
        if connector is None:
            return False

        connector.models = models
        self._save()

        log.debug("Updated connector models", connector_id=connector_id, models=models)
        return True

    def validate_api_key(self, api_key: str) -> Connector | None:
        """Validate an API key and return the connector if valid and approved.

        Args:
            api_key: The API key to validate

        Returns:
            The Connector if valid and approved, None otherwise
        """
        connector = self._api_key_index.get(api_key)
        if connector is None or connector.status != ConnectorStatus.APPROVED:
            return None
        return connector
