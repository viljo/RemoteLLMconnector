"""Unit tests for the ConnectorStore and Connector model."""

import tempfile
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from remotellm.broker.connectors import Connector, ConnectorStatus, ConnectorStore


class TestConnectorStatus:
    """Tests for the ConnectorStatus enum."""

    def test_status_values(self):
        """Test that all expected status values exist."""
        assert ConnectorStatus.PENDING.value == "pending"
        assert ConnectorStatus.APPROVED.value == "approved"
        assert ConnectorStatus.REVOKED.value == "revoked"

    def test_status_from_string(self):
        """Test creating status from string."""
        assert ConnectorStatus("pending") == ConnectorStatus.PENDING
        assert ConnectorStatus("approved") == ConnectorStatus.APPROVED
        assert ConnectorStatus("revoked") == ConnectorStatus.REVOKED


class TestConnector:
    """Tests for the Connector dataclass."""

    def test_connector_creation_defaults(self):
        """Test creating a connector with default values."""
        connector = Connector(
            connector_id="conn-123",
            api_key=None,
        )

        assert connector.connector_id == "conn-123"
        assert connector.api_key is None
        assert connector.name is None
        assert connector.models == []
        assert connector.status == ConnectorStatus.PENDING
        assert isinstance(connector.created_at, datetime)
        assert connector.last_used is None
        assert connector.last_connected is None

    def test_connector_creation_with_values(self):
        """Test creating a connector with all values."""
        created = datetime(2024, 1, 15, 10, 0, 0)
        last_used = datetime(2024, 1, 15, 14, 0, 0)
        last_connected = datetime(2024, 1, 15, 13, 0, 0)

        connector = Connector(
            connector_id="conn-456",
            api_key="ck-test123",
            name="Test Connector",
            models=["gpt-4", "llama3"],
            status=ConnectorStatus.APPROVED,
            created_at=created,
            last_used=last_used,
            last_connected=last_connected,
        )

        assert connector.connector_id == "conn-456"
        assert connector.api_key == "ck-test123"
        assert connector.name == "Test Connector"
        assert connector.models == ["gpt-4", "llama3"]
        assert connector.status == ConnectorStatus.APPROVED
        assert connector.created_at == created
        assert connector.last_used == last_used
        assert connector.last_connected == last_connected

    def test_connector_to_dict(self):
        """Test serializing connector to dictionary."""
        connector = Connector(
            connector_id="conn-789",
            api_key="ck-key",
            name="My Connector",
            models=["model-a", "model-b"],
            status=ConnectorStatus.APPROVED,
        )

        data = connector.to_dict()

        assert data["connector_id"] == "conn-789"
        assert data["api_key"] == "ck-key"
        assert data["name"] == "My Connector"
        assert data["models"] == ["model-a", "model-b"]
        assert data["status"] == "approved"
        assert "created_at" in data
        assert data["last_used"] is None
        assert data["last_connected"] is None

    def test_connector_to_dict_with_timestamps(self):
        """Test serializing connector with all timestamps."""
        created = datetime(2024, 1, 15, 10, 0, 0)
        last_used = datetime(2024, 1, 15, 14, 0, 0)
        last_connected = datetime(2024, 1, 15, 13, 0, 0)

        connector = Connector(
            connector_id="conn-ts",
            api_key="ck-key",
            created_at=created,
            last_used=last_used,
            last_connected=last_connected,
        )

        data = connector.to_dict()

        assert data["created_at"] == created.isoformat()
        assert data["last_used"] == last_used.isoformat()
        assert data["last_connected"] == last_connected.isoformat()

    def test_connector_from_dict(self):
        """Test deserializing connector from dictionary."""
        data = {
            "connector_id": "conn-dict",
            "api_key": "ck-fromdict",
            "name": "From Dict",
            "models": ["model1", "model2"],
            "status": "approved",
            "created_at": "2024-01-15T10:30:00",
            "last_used": "2024-01-15T14:00:00",
            "last_connected": "2024-01-15T13:30:00",
        }

        connector = Connector.from_dict(data)

        assert connector.connector_id == "conn-dict"
        assert connector.api_key == "ck-fromdict"
        assert connector.name == "From Dict"
        assert connector.models == ["model1", "model2"]
        assert connector.status == ConnectorStatus.APPROVED
        assert connector.last_used is not None
        assert connector.last_connected is not None

    def test_connector_from_dict_defaults(self):
        """Test deserializing connector with missing optional fields."""
        data = {
            "connector_id": "conn-minimal",
            "created_at": "2024-01-01T00:00:00",
        }

        connector = Connector.from_dict(data)

        assert connector.connector_id == "conn-minimal"
        assert connector.api_key is None
        assert connector.name is None
        assert connector.models == []
        assert connector.status == ConnectorStatus.PENDING
        assert connector.last_used is None
        assert connector.last_connected is None

    def test_connector_from_dict_pending_status(self):
        """Test that default status is pending."""
        data = {
            "connector_id": "conn-pending",
            "created_at": "2024-01-01T00:00:00",
        }

        connector = Connector.from_dict(data)
        assert connector.status == ConnectorStatus.PENDING


class TestConnectorStore:
    """Tests for the ConnectorStore class."""

    def test_store_initialization_memory_only(self):
        """Test creating a store in memory-only mode."""
        store = ConnectorStore(file_path=None)

        assert store.file_path is None
        assert len(store.get_all()) == 0

    def test_store_initialization_with_file(self):
        """Test creating a store with a file path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "connectors.yaml"
            store = ConnectorStore(file_path)

            assert store.file_path == file_path
            assert len(store.get_all()) == 0

    def test_create_pending(self):
        """Test creating a pending connector."""
        store = ConnectorStore(file_path=None)

        connector = store.create_pending(
            models=["gpt-4", "llama3"],
            name="Test Connector",
        )

        assert connector.connector_id.startswith("conn-")
        assert connector.api_key is None
        assert connector.name == "Test Connector"
        assert connector.models == ["gpt-4", "llama3"]
        assert connector.status == ConnectorStatus.PENDING
        assert connector.last_connected is not None

    def test_create_pending_minimal(self):
        """Test creating pending connector with minimal args."""
        store = ConnectorStore(file_path=None)

        connector = store.create_pending()

        assert connector.connector_id.startswith("conn-")
        assert connector.api_key is None
        assert connector.name is None
        assert connector.models == []
        assert connector.status == ConnectorStatus.PENDING

    def test_approve_pending_connector(self):
        """Test approving a pending connector."""
        store = ConnectorStore(file_path=None)
        connector = store.create_pending(models=["gpt-4"])

        api_key = store.approve(connector.connector_id)

        assert api_key is not None
        assert api_key.startswith("ck-")
        assert len(api_key) == 35  # "ck-" + 32 hex chars

        # Verify connector was updated
        updated = store.get_by_id(connector.connector_id)
        assert updated.status == ConnectorStatus.APPROVED
        assert updated.api_key == api_key

    def test_approve_nonexistent_connector(self):
        """Test approving a connector that doesn't exist."""
        store = ConnectorStore(file_path=None)

        api_key = store.approve("conn-nonexistent")

        assert api_key is None

    def test_approve_already_approved_connector(self):
        """Test approving a connector that's already approved."""
        store = ConnectorStore(file_path=None)
        connector = store.create_pending()
        first_key = store.approve(connector.connector_id)

        # Try to approve again
        second_key = store.approve(connector.connector_id)

        assert second_key is None
        # Original key should still be valid
        found = store.get_by_api_key(first_key)
        assert found is not None

    def test_approve_revoked_connector(self):
        """Test that revoked connectors cannot be approved."""
        store = ConnectorStore(file_path=None)
        connector = store.create_pending()
        store.approve(connector.connector_id)
        store.revoke(connector.connector_id)

        # Try to approve after revocation
        api_key = store.approve(connector.connector_id)

        assert api_key is None

    def test_revoke_connector(self):
        """Test revoking a connector."""
        store = ConnectorStore(file_path=None)
        connector = store.create_pending()
        api_key = store.approve(connector.connector_id)

        result = store.revoke(connector.connector_id, reason="Test revocation")

        assert result is True
        updated = store.get_by_id(connector.connector_id)
        assert updated.status == ConnectorStatus.REVOKED

        # API key should no longer work
        found = store.get_by_api_key(api_key)
        assert found is None

    def test_revoke_pending_connector(self):
        """Test revoking a pending connector."""
        store = ConnectorStore(file_path=None)
        connector = store.create_pending()

        result = store.revoke(connector.connector_id)

        assert result is True
        updated = store.get_by_id(connector.connector_id)
        assert updated.status == ConnectorStatus.REVOKED

    def test_revoke_nonexistent_connector(self):
        """Test revoking a connector that doesn't exist."""
        store = ConnectorStore(file_path=None)

        result = store.revoke("conn-nonexistent")

        assert result is False

    def test_get_by_api_key(self):
        """Test retrieving connector by API key."""
        store = ConnectorStore(file_path=None)
        connector = store.create_pending()
        api_key = store.approve(connector.connector_id)

        found = store.get_by_api_key(api_key)

        assert found is not None
        assert found.connector_id == connector.connector_id
        assert found.api_key == api_key

    def test_get_by_api_key_not_found(self):
        """Test retrieving connector with invalid API key."""
        store = ConnectorStore(file_path=None)

        found = store.get_by_api_key("ck-invalid")

        assert found is None

    def test_get_by_api_key_pending_connector(self):
        """Test that pending connectors have no API key."""
        store = ConnectorStore(file_path=None)
        connector = store.create_pending()

        # Pending connector has no API key
        assert connector.api_key is None
        found = store.get_by_api_key("ck-anything")
        assert found is None

    def test_get_by_connector_id(self):
        """Test retrieving connector by ID."""
        store = ConnectorStore(file_path=None)
        connector = store.create_pending(name="Find Me")

        found = store.get_by_id(connector.connector_id)

        assert found is not None
        assert found.connector_id == connector.connector_id
        assert found.name == "Find Me"

    def test_get_by_connector_id_not_found(self):
        """Test retrieving connector with invalid ID."""
        store = ConnectorStore(file_path=None)

        found = store.get_by_id("conn-invalid")

        assert found is None

    def test_get_pending(self):
        """Test retrieving all pending connectors."""
        store = ConnectorStore(file_path=None)

        # Create connectors with different statuses
        pending1 = store.create_pending(name="Pending 1")
        pending2 = store.create_pending(name="Pending 2")
        approved = store.create_pending(name="Approved")
        store.approve(approved.connector_id)
        revoked = store.create_pending(name="Revoked")
        store.approve(revoked.connector_id)
        store.revoke(revoked.connector_id)

        pending_list = store.get_pending()

        assert len(pending_list) == 2
        ids = {c.connector_id for c in pending_list}
        assert pending1.connector_id in ids
        assert pending2.connector_id in ids

    def test_get_approved(self):
        """Test retrieving all approved connectors."""
        store = ConnectorStore(file_path=None)

        # Create connectors with different statuses
        pending = store.create_pending(name="Pending")
        approved1 = store.create_pending(name="Approved 1")
        store.approve(approved1.connector_id)
        approved2 = store.create_pending(name="Approved 2")
        store.approve(approved2.connector_id)
        revoked = store.create_pending(name="Revoked")
        store.approve(revoked.connector_id)
        store.revoke(revoked.connector_id)

        approved_list = store.get_approved()

        assert len(approved_list) == 2
        ids = {c.connector_id for c in approved_list}
        assert approved1.connector_id in ids
        assert approved2.connector_id in ids

    def test_get_revoked(self):
        """Test retrieving all revoked connectors."""
        store = ConnectorStore(file_path=None)

        # Create connectors with different statuses
        pending = store.create_pending(name="Pending")
        approved = store.create_pending(name="Approved")
        store.approve(approved.connector_id)
        revoked1 = store.create_pending(name="Revoked 1")
        store.approve(revoked1.connector_id)
        store.revoke(revoked1.connector_id)
        revoked2 = store.create_pending(name="Revoked 2")
        store.revoke(revoked2.connector_id)

        revoked_list = store.get_revoked()

        assert len(revoked_list) == 2
        ids = {c.connector_id for c in revoked_list}
        assert revoked1.connector_id in ids
        assert revoked2.connector_id in ids

    def test_update_models(self):
        """Test updating connector models."""
        store = ConnectorStore(file_path=None)
        connector = store.create_pending(models=["model-a"])

        result = store.update_models(connector.connector_id, ["model-b", "model-c"])

        assert result is True
        updated = store.get_by_id(connector.connector_id)
        assert updated.models == ["model-b", "model-c"]

    def test_update_models_empty_list(self):
        """Test updating to empty model list."""
        store = ConnectorStore(file_path=None)
        connector = store.create_pending(models=["model-a", "model-b"])

        result = store.update_models(connector.connector_id, [])

        assert result is True
        updated = store.get_by_id(connector.connector_id)
        assert updated.models == []

    def test_update_models_nonexistent_connector(self):
        """Test updating models for nonexistent connector."""
        store = ConnectorStore(file_path=None)

        result = store.update_models("conn-nonexistent", ["model-x"])

        assert result is False

    def test_update_last_connected(self):
        """Test updating last_connected timestamp."""
        store = ConnectorStore(file_path=None)
        connector = store.create_pending()
        original_time = connector.last_connected

        # Small delay to ensure timestamp changes
        import time
        time.sleep(0.01)

        store.update_last_connected(connector)

        assert connector.last_connected is not None
        assert isinstance(connector.last_connected, datetime)
        assert connector.last_connected > original_time

    def test_update_last_used(self):
        """Test updating last_used timestamp."""
        store = ConnectorStore(file_path=None)
        connector = store.create_pending()
        assert connector.last_used is None

        store.update_last_used(connector)

        assert connector.last_used is not None
        assert isinstance(connector.last_used, datetime)

    def test_validate_api_key_approved(self):
        """Test validating an approved connector's API key."""
        store = ConnectorStore(file_path=None)
        connector = store.create_pending()
        api_key = store.approve(connector.connector_id)

        validated = store.validate_api_key(api_key)

        assert validated is not None
        assert validated.connector_id == connector.connector_id
        assert validated.status == ConnectorStatus.APPROVED

    def test_validate_api_key_invalid(self):
        """Test validating an invalid API key."""
        store = ConnectorStore(file_path=None)

        validated = store.validate_api_key("ck-invalid")

        assert validated is None

    def test_validate_api_key_pending(self):
        """Test that pending connectors fail validation."""
        store = ConnectorStore(file_path=None)
        connector = store.create_pending()

        # Pending connector has no API key
        validated = store.validate_api_key("ck-anything")

        assert validated is None

    def test_validate_api_key_revoked(self):
        """Test that revoked connectors fail validation."""
        store = ConnectorStore(file_path=None)
        connector = store.create_pending()
        api_key = store.approve(connector.connector_id)
        store.revoke(connector.connector_id)

        validated = store.validate_api_key(api_key)

        assert validated is None

    def test_delete_connector(self):
        """Test deleting a connector."""
        store = ConnectorStore(file_path=None)
        connector = store.create_pending()
        api_key = store.approve(connector.connector_id)

        result = store.delete(connector.connector_id)

        assert result is True
        assert store.get_by_id(connector.connector_id) is None
        assert store.get_by_api_key(api_key) is None

    def test_delete_nonexistent_connector(self):
        """Test deleting a nonexistent connector."""
        store = ConnectorStore(file_path=None)

        result = store.delete("conn-nonexistent")

        assert result is False

    def test_get_all(self):
        """Test retrieving all connectors."""
        store = ConnectorStore(file_path=None)

        connector1 = store.create_pending(name="Conn 1")
        connector2 = store.create_pending(name="Conn 2")
        connector3 = store.create_pending(name="Conn 3")

        all_connectors = store.get_all()

        assert len(all_connectors) == 3
        ids = {c.connector_id for c in all_connectors}
        assert connector1.connector_id in ids
        assert connector2.connector_id in ids
        assert connector3.connector_id in ids


class TestConnectorStorePersistence:
    """Tests for YAML persistence in ConnectorStore."""

    def test_persistence_save_and_load(self):
        """Test that connectors are persisted to YAML file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "connectors.yaml"

            # Create store and add connectors
            store1 = ConnectorStore(file_path)
            pending = store1.create_pending(name="Pending Conn", models=["model-a"])
            approved = store1.create_pending(name="Approved Conn", models=["model-b"])
            api_key = store1.approve(approved.connector_id)

            # Create new store from same file
            store2 = ConnectorStore(file_path)

            assert len(store2.get_all()) == 2

            pending_loaded = store2.get_by_id(pending.connector_id)
            approved_loaded = store2.get_by_id(approved.connector_id)

            assert pending_loaded is not None
            assert pending_loaded.status == ConnectorStatus.PENDING
            assert pending_loaded.name == "Pending Conn"
            assert pending_loaded.models == ["model-a"]
            assert pending_loaded.api_key is None

            assert approved_loaded is not None
            assert approved_loaded.status == ConnectorStatus.APPROVED
            assert approved_loaded.name == "Approved Conn"
            assert approved_loaded.models == ["model-b"]
            assert approved_loaded.api_key == api_key

            # Test API key lookup works
            found = store2.get_by_api_key(api_key)
            assert found is not None
            assert found.connector_id == approved.connector_id

    def test_persistence_yaml_format(self):
        """Test the YAML file format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "connectors.yaml"

            store = ConnectorStore(file_path)
            connector = store.create_pending(
                name="YAML Connector",
                models=["model-x", "model-y"],
            )
            api_key = store.approve(connector.connector_id)

            # Read the YAML file directly
            with open(file_path) as f:
                data = yaml.safe_load(f)

            assert "connectors" in data
            assert len(data["connectors"]) == 1
            conn_data = data["connectors"][0]
            assert conn_data["connector_id"] == connector.connector_id
            assert conn_data["api_key"] == api_key
            assert conn_data["name"] == "YAML Connector"
            assert conn_data["models"] == ["model-x", "model-y"]
            assert conn_data["status"] == "approved"
            assert "created_at" in conn_data

    def test_persistence_multiple_connectors(self):
        """Test persisting multiple connectors."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "connectors.yaml"

            store1 = ConnectorStore(file_path)
            conn1 = store1.create_pending(name="Conn 1")
            conn2 = store1.create_pending(name="Conn 2")
            conn3 = store1.create_pending(name="Conn 3")
            store1.approve(conn2.connector_id)
            store1.revoke(conn3.connector_id)

            # Load in new store
            store2 = ConnectorStore(file_path)

            assert len(store2.get_all()) == 3
            assert len(store2.get_pending()) == 1
            assert len(store2.get_approved()) == 1
            assert len(store2.get_revoked()) == 1

    def test_persistence_updates(self):
        """Test that updates are persisted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "connectors.yaml"

            store1 = ConnectorStore(file_path)
            connector = store1.create_pending(models=["old-model"])

            # Update models
            store1.update_models(connector.connector_id, ["new-model"])

            # Load in new store
            store2 = ConnectorStore(file_path)
            loaded = store2.get_by_id(connector.connector_id)

            assert loaded.models == ["new-model"]

    def test_persistence_directory_creation(self):
        """Test that parent directories are created if needed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "subdir" / "connectors.yaml"

            store = ConnectorStore(file_path)
            store.create_pending(name="Test")

            assert file_path.exists()
            assert file_path.parent.exists()

    def test_load_nonexistent_file(self):
        """Test loading from a file that doesn't exist."""
        store = ConnectorStore(Path("/nonexistent/path/connectors.yaml"))

        # Should start with empty store, no error
        assert len(store.get_all()) == 0

    def test_load_empty_file(self):
        """Test loading from an empty YAML file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "empty.yaml"
            file_path.write_text("")

            store = ConnectorStore(file_path)

            # Should start with empty store
            assert len(store.get_all()) == 0

    def test_persistence_timestamps(self):
        """Test that timestamps are persisted correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "connectors.yaml"

            store1 = ConnectorStore(file_path)
            connector = store1.create_pending()
            store1.approve(connector.connector_id)
            store1.update_last_used(connector)
            store1.update_last_connected(connector)

            original_created = connector.created_at
            original_used = connector.last_used
            original_connected = connector.last_connected

            # Load in new store
            store2 = ConnectorStore(file_path)
            loaded = store2.get_by_id(connector.connector_id)

            assert loaded.created_at == original_created
            assert loaded.last_used == original_used
            assert loaded.last_connected == original_connected

    def test_api_key_index_rebuilt_on_load(self):
        """Test that API key index is rebuilt when loading from file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "connectors.yaml"

            # Create and save
            store1 = ConnectorStore(file_path)
            connector = store1.create_pending()
            api_key = store1.approve(connector.connector_id)

            # Load in new store
            store2 = ConnectorStore(file_path)

            # API key lookup should work
            found = store2.get_by_api_key(api_key)
            assert found is not None
            assert found.connector_id == connector.connector_id

    def test_pending_connectors_have_no_api_key_index(self):
        """Test that pending connectors are not added to API key index."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "connectors.yaml"

            store1 = ConnectorStore(file_path)
            pending = store1.create_pending()

            # Reload
            store2 = ConnectorStore(file_path)

            # Pending connector exists
            assert store2.get_by_id(pending.connector_id) is not None
            # But has no API key
            assert pending.api_key is None


class TestConnectorStoreErrorHandling:
    """Tests for error handling in ConnectorStore."""

    def test_connector_not_found_errors(self):
        """Test operations on nonexistent connectors return appropriate values."""
        store = ConnectorStore(file_path=None)

        assert store.get_by_id("conn-invalid") is None
        assert store.approve("conn-invalid") is None
        assert store.revoke("conn-invalid") is False
        assert store.update_models("conn-invalid", ["model"]) is False
        assert store.delete("conn-invalid") is False

    def test_unique_connector_ids(self):
        """Test that each created connector gets a unique ID."""
        store = ConnectorStore(file_path=None)

        connectors = [store.create_pending() for _ in range(10)]
        ids = [c.connector_id for c in connectors]

        # All IDs should be unique
        assert len(ids) == len(set(ids))

    def test_unique_api_keys(self):
        """Test that each approved connector gets a unique API key."""
        store = ConnectorStore(file_path=None)

        connectors = [store.create_pending() for _ in range(10)]
        api_keys = [store.approve(c.connector_id) for c in connectors]

        # All API keys should be unique
        assert len(api_keys) == len(set(api_keys))

    def test_api_key_format(self):
        """Test that generated API keys have correct format."""
        store = ConnectorStore(file_path=None)
        connector = store.create_pending()

        api_key = store.approve(connector.connector_id)

        assert api_key is not None
        assert api_key.startswith("ck-")
        assert len(api_key) == 35  # "ck-" + 32 hex chars
        # Verify it's hex after the prefix
        hex_part = api_key[3:]
        assert all(c in "0123456789abcdef" for c in hex_part)

    def test_connector_id_format(self):
        """Test that generated connector IDs have correct format."""
        store = ConnectorStore(file_path=None)
        connector = store.create_pending()

        assert connector.connector_id.startswith("conn-")
        # Should have 8 hex chars after prefix
        hex_part = connector.connector_id[5:]
        assert len(hex_part) == 8
        assert all(c in "0123456789abcdef" for c in hex_part)
