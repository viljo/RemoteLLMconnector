"""Unit tests for the admin module components."""

import pytest

from remotellm.broker.admin import RequestLog, RequestLogger


class TestRequestLogger:
    """Tests for the RequestLogger class."""

    def test_log_request(self):
        """Test logging a request."""
        logger = RequestLogger(max_logs=100)

        logger.log_request(
            correlation_id="corr-123",
            user="testuser",
            model="gpt-4",
            status="success",
            duration_ms=150,
        )

        logs = logger.get_logs()
        assert len(logs) == 1
        assert logs[0].correlation_id == "corr-123"
        assert logs[0].user == "testuser"
        assert logs[0].model == "gpt-4"
        assert logs[0].status == "success"
        assert logs[0].duration_ms == 150

    def test_logs_ordered_newest_first(self):
        """Test that logs are returned newest first."""
        logger = RequestLogger(max_logs=100)

        logger.log_request("first", "user1", "model1", "success", 100)
        logger.log_request("second", "user2", "model2", "success", 200)
        logger.log_request("third", "user3", "model3", "error", 300)

        logs = logger.get_logs()
        assert len(logs) == 3
        assert logs[0].correlation_id == "third"
        assert logs[1].correlation_id == "second"
        assert logs[2].correlation_id == "first"

    def test_max_logs_limit(self):
        """Test that logs are limited to max_logs."""
        logger = RequestLogger(max_logs=3)

        for i in range(5):
            logger.log_request(f"log-{i}", None, "model", "success", 100)

        logs = logger.get_logs()
        assert len(logs) == 3
        # Should have the 3 most recent
        assert logs[0].correlation_id == "log-4"
        assert logs[1].correlation_id == "log-3"
        assert logs[2].correlation_id == "log-2"

    def test_filter_by_user(self):
        """Test filtering logs by user."""
        logger = RequestLogger()

        logger.log_request("1", "alice", "model", "success", 100)
        logger.log_request("2", "bob", "model", "success", 100)
        logger.log_request("3", "alice", "model", "error", 100)
        logger.log_request("4", None, "model", "success", 100)

        alice_logs = logger.get_logs(user="alice")
        assert len(alice_logs) == 2
        assert all(log.user == "alice" for log in alice_logs)

        bob_logs = logger.get_logs(user="bob")
        assert len(bob_logs) == 1
        assert bob_logs[0].correlation_id == "2"

    def test_filter_by_model(self):
        """Test filtering logs by model."""
        logger = RequestLogger()

        logger.log_request("1", None, "gpt-4", "success", 100)
        logger.log_request("2", None, "claude-3", "success", 100)
        logger.log_request("3", None, "gpt-4", "error", 100)

        gpt_logs = logger.get_logs(model="gpt-4")
        assert len(gpt_logs) == 2
        assert all(log.model == "gpt-4" for log in gpt_logs)

    def test_filter_by_status(self):
        """Test filtering logs by status."""
        logger = RequestLogger()

        logger.log_request("1", None, "model", "success", 100)
        logger.log_request("2", None, "model", "error", 100)
        logger.log_request("3", None, "model", "success", 100)
        logger.log_request("4", None, "model", "timeout", 100)

        success_logs = logger.get_logs(status="success")
        assert len(success_logs) == 2

        error_logs = logger.get_logs(status="error")
        assert len(error_logs) == 1

    def test_filter_by_correlation_id(self):
        """Test filtering logs by correlation ID (partial match)."""
        logger = RequestLogger()

        logger.log_request("abc-123-xyz", None, "model", "success", 100)
        logger.log_request("def-456-xyz", None, "model", "success", 100)
        logger.log_request("abc-789-zzz", None, "model", "success", 100)

        # Partial match on "abc"
        abc_logs = logger.get_logs(correlation_id="abc")
        assert len(abc_logs) == 2

        # Partial match on "xyz"
        xyz_logs = logger.get_logs(correlation_id="xyz")
        assert len(xyz_logs) == 2

        # Exact partial match
        exact_logs = logger.get_logs(correlation_id="123")
        assert len(exact_logs) == 1

    def test_filter_combined(self):
        """Test combining multiple filters."""
        logger = RequestLogger()

        logger.log_request("1", "alice", "gpt-4", "success", 100)
        logger.log_request("2", "alice", "gpt-4", "error", 100)
        logger.log_request("3", "bob", "gpt-4", "success", 100)
        logger.log_request("4", "alice", "claude", "success", 100)

        # alice + gpt-4 + success
        logs = logger.get_logs(user="alice", model="gpt-4", status="success")
        assert len(logs) == 1
        assert logs[0].correlation_id == "1"

    def test_empty_logs(self):
        """Test getting logs when none exist."""
        logger = RequestLogger()

        logs = logger.get_logs()
        assert logs == []

    def test_filter_no_match(self):
        """Test filtering with no matching logs."""
        logger = RequestLogger()
        logger.log_request("1", "alice", "gpt-4", "success", 100)

        logs = logger.get_logs(user="bob")
        assert logs == []

    def test_log_with_none_user(self):
        """Test logging requests with no user (anonymous)."""
        logger = RequestLogger()

        logger.log_request("anon-1", None, "model", "success", 100)

        logs = logger.get_logs()
        assert len(logs) == 1
        assert logs[0].user is None

    def test_timestamp_auto_generated(self):
        """Test that timestamp is automatically generated."""
        logger = RequestLogger()

        logger.log_request("1", None, "model", "success", 100)

        logs = logger.get_logs()
        assert logs[0].timestamp is not None


class TestRequestLog:
    """Tests for the RequestLog dataclass."""

    def test_request_log_creation(self):
        """Test creating a RequestLog."""
        from datetime import datetime

        log = RequestLog(
            timestamp=datetime.utcnow(),
            correlation_id="test-id",
            user="testuser",
            model="gpt-4",
            status="success",
            duration_ms=250,
        )

        assert log.correlation_id == "test-id"
        assert log.user == "testuser"
        assert log.model == "gpt-4"
        assert log.status == "success"
        assert log.duration_ms == 250
