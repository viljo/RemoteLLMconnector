"""Unit tests for the relay protocol module."""

import pytest

from remotellm.shared.protocol import (
    AuthFailPayload,
    AuthOkPayload,
    AuthPayload,
    ApprovedPayload,
    ErrorPayload,
    MessageType,
    PendingPayload,
    RequestPayload,
    ResponsePayload,
    RevokedPayload,
    StreamChunkPayload,
    StreamEndPayload,
    RelayMessage,
    create_approved_message,
    create_auth_fail_message,
    create_auth_message,
    create_auth_ok_message,
    create_cancel_message,
    create_error_message,
    create_pending_message,
    create_ping_message,
    create_pong_message,
    create_request_message,
    create_response_message,
    create_revoked_message,
    create_stream_chunk_message,
    create_stream_end_message,
)


class TestMessageType:
    """Tests for MessageType enum."""

    def test_connector_to_broker_types(self):
        """Test connector-to-broker message types exist."""
        assert MessageType.AUTH == "AUTH"
        assert MessageType.RESPONSE == "RESPONSE"
        assert MessageType.STREAM_CHUNK == "STREAM_CHUNK"
        assert MessageType.STREAM_END == "STREAM_END"
        assert MessageType.ERROR == "ERROR"
        assert MessageType.PING == "PING"

    def test_broker_to_connector_types(self):
        """Test broker-to-connector message types exist."""
        assert MessageType.AUTH_OK == "AUTH_OK"
        assert MessageType.AUTH_FAIL == "AUTH_FAIL"
        assert MessageType.REQUEST == "REQUEST"
        assert MessageType.PONG == "PONG"
        assert MessageType.CANCEL == "CANCEL"
        assert MessageType.PENDING == "PENDING"
        assert MessageType.APPROVED == "APPROVED"
        assert MessageType.REVOKED == "REVOKED"

    def test_message_type_is_string(self):
        """Test that MessageType values are strings."""
        for msg_type in MessageType:
            assert isinstance(msg_type.value, str)


class TestRelayMessage:
    """Tests for RelayMessage model."""

    def test_create_message(self):
        """Test creating a basic RelayMessage."""
        msg = RelayMessage(type=MessageType.PING, id="test-123")
        assert msg.type == MessageType.PING
        assert msg.id == "test-123"
        assert msg.payload == {}

    def test_create_message_with_payload(self):
        """Test creating a RelayMessage with payload."""
        msg = RelayMessage(
            type=MessageType.AUTH,
            id="auth-456",
            payload={"token": "secret", "models": ["llama3"]},
        )
        assert msg.type == MessageType.AUTH
        assert msg.payload["token"] == "secret"
        assert msg.payload["models"] == ["llama3"]

    def test_message_serialization(self):
        """Test message can be serialized to dict."""
        msg = RelayMessage(
            type=MessageType.RESPONSE,
            id="resp-789",
            payload={"status": 200},
        )
        data = msg.model_dump()
        assert data["type"] == "RESPONSE"
        assert data["id"] == "resp-789"
        assert data["payload"]["status"] == 200

    def test_message_json_serialization(self):
        """Test message can be serialized to JSON."""
        msg = RelayMessage(type=MessageType.PING, id="ping-1")
        json_str = msg.model_dump_json()
        assert '"type":"PING"' in json_str
        assert '"id":"ping-1"' in json_str

    def test_message_deserialization(self):
        """Test message can be deserialized from dict."""
        data = {"type": "AUTH_OK", "id": "test-1", "payload": {"session_id": "sess-123"}}
        msg = RelayMessage.model_validate(data)
        assert msg.type == MessageType.AUTH_OK
        assert msg.id == "test-1"
        assert msg.payload["session_id"] == "sess-123"


class TestPayloads:
    """Tests for payload models."""

    def test_auth_payload(self):
        """Test AuthPayload model."""
        payload = AuthPayload(token="my-token", name="test-connector", models=["gpt-4", "llama3"])
        assert payload.token == "my-token"
        assert payload.name == "test-connector"
        assert payload.models == ["gpt-4", "llama3"]
        assert payload.connector_version == "1.0.0"

    def test_auth_payload_defaults(self):
        """Test AuthPayload defaults."""
        payload = AuthPayload()
        assert payload.token is None
        assert payload.name is None
        assert payload.models == []
        assert payload.connector_version == "1.0.0"

    def test_auth_ok_payload(self):
        """Test AuthOkPayload model."""
        payload = AuthOkPayload(session_id="sess-abc")
        assert payload.session_id == "sess-abc"

    def test_auth_fail_payload(self):
        """Test AuthFailPayload model."""
        payload = AuthFailPayload(error="Invalid token")
        assert payload.error == "Invalid token"

    def test_pending_payload(self):
        """Test PendingPayload model."""
        payload = PendingPayload(connector_id="conn-123")
        assert payload.connector_id == "conn-123"
        assert payload.message == "Waiting for admin approval"

    def test_pending_payload_custom_message(self):
        """Test PendingPayload with custom message."""
        payload = PendingPayload(connector_id="conn-123", message="Please wait")
        assert payload.message == "Please wait"

    def test_approved_payload(self):
        """Test ApprovedPayload model."""
        payload = ApprovedPayload(api_key="ck-generated-key")
        assert payload.api_key == "ck-generated-key"

    def test_revoked_payload(self):
        """Test RevokedPayload model."""
        payload = RevokedPayload()
        assert payload.reason == "API key revoked by admin"

    def test_revoked_payload_custom_reason(self):
        """Test RevokedPayload with custom reason."""
        payload = RevokedPayload(reason="Security concern")
        assert payload.reason == "Security concern"

    def test_request_payload(self):
        """Test RequestPayload model."""
        payload = RequestPayload(
            method="POST",
            path="/v1/chat/completions",
            headers={"Content-Type": "application/json"},
            body="eyJtb2RlbCI6ICJncHQtNCJ9",  # base64
            llm_api_key="sk-llm-key",
        )
        assert payload.method == "POST"
        assert payload.path == "/v1/chat/completions"
        assert payload.headers["Content-Type"] == "application/json"
        assert payload.llm_api_key == "sk-llm-key"

    def test_request_payload_defaults(self):
        """Test RequestPayload defaults."""
        payload = RequestPayload(method="GET", path="/v1/models", headers={})
        assert payload.body == ""
        assert payload.llm_api_key is None

    def test_response_payload(self):
        """Test ResponsePayload model."""
        payload = ResponsePayload(
            status=200,
            headers={"Content-Type": "application/json"},
            body="eyJyZXN1bHQiOiAib2sifQ==",
        )
        assert payload.status == 200
        assert payload.headers["Content-Type"] == "application/json"

    def test_stream_chunk_payload(self):
        """Test StreamChunkPayload model."""
        payload = StreamChunkPayload(chunk="data: {\"choices\": []}\n\n")
        assert payload.chunk == "data: {\"choices\": []}\n\n"
        assert payload.done is False

    def test_stream_chunk_payload_done(self):
        """Test StreamChunkPayload with done flag."""
        payload = StreamChunkPayload(chunk="data: [DONE]\n\n", done=True)
        assert payload.done is True

    def test_stream_end_payload(self):
        """Test StreamEndPayload model."""
        payload = StreamEndPayload()
        assert payload.done is True

    def test_error_payload(self):
        """Test ErrorPayload model."""
        payload = ErrorPayload(status=500, error="Internal error", code="internal_error")
        assert payload.status == 500
        assert payload.error == "Internal error"
        assert payload.code == "internal_error"


class TestMessageFactories:
    """Tests for message factory functions."""

    def test_create_auth_message(self):
        """Test create_auth_message factory."""
        msg = create_auth_message(
            correlation_id="auth-1",
            token="my-token",
            models=["llama3", "gpt-4"],
            name="test-connector",
        )
        assert msg.type == MessageType.AUTH
        assert msg.id == "auth-1"
        assert msg.payload["token"] == "my-token"
        assert msg.payload["models"] == ["llama3", "gpt-4"]
        assert msg.payload["name"] == "test-connector"

    def test_create_auth_message_minimal(self):
        """Test create_auth_message with minimal args."""
        msg = create_auth_message(correlation_id="auth-2")
        assert msg.type == MessageType.AUTH
        assert msg.payload["token"] is None
        assert msg.payload["models"] == []

    def test_create_auth_ok_message(self):
        """Test create_auth_ok_message factory."""
        msg = create_auth_ok_message(correlation_id="auth-ok-1", session_id="sess-abc")
        assert msg.type == MessageType.AUTH_OK
        assert msg.id == "auth-ok-1"
        assert msg.payload["session_id"] == "sess-abc"

    def test_create_auth_fail_message(self):
        """Test create_auth_fail_message factory."""
        msg = create_auth_fail_message(correlation_id="auth-fail-1", error="Bad token")
        assert msg.type == MessageType.AUTH_FAIL
        assert msg.payload["error"] == "Bad token"

    def test_create_request_message(self):
        """Test create_request_message factory."""
        msg = create_request_message(
            correlation_id="req-1",
            method="POST",
            path="/v1/chat/completions",
            headers={"Authorization": "Bearer sk-test"},
            body="eyJ9",
            llm_api_key="sk-llm",
        )
        assert msg.type == MessageType.REQUEST
        assert msg.payload["method"] == "POST"
        assert msg.payload["path"] == "/v1/chat/completions"
        assert msg.payload["llm_api_key"] == "sk-llm"

    def test_create_response_message(self):
        """Test create_response_message factory."""
        msg = create_response_message(
            correlation_id="resp-1",
            status=200,
            headers={"Content-Type": "application/json"},
            body="eyJvayI6IHRydWV9",
        )
        assert msg.type == MessageType.RESPONSE
        assert msg.payload["status"] == 200

    def test_create_stream_chunk_message(self):
        """Test create_stream_chunk_message factory."""
        msg = create_stream_chunk_message(
            correlation_id="stream-1",
            chunk="data: test\n\n",
            done=False,
        )
        assert msg.type == MessageType.STREAM_CHUNK
        assert msg.payload["chunk"] == "data: test\n\n"
        assert msg.payload["done"] is False

    def test_create_stream_end_message(self):
        """Test create_stream_end_message factory."""
        msg = create_stream_end_message(correlation_id="stream-end-1")
        assert msg.type == MessageType.STREAM_END
        assert msg.payload["done"] is True

    def test_create_error_message(self):
        """Test create_error_message factory."""
        msg = create_error_message(
            correlation_id="err-1",
            status=404,
            error="Model not found",
            code="model_not_found",
        )
        assert msg.type == MessageType.ERROR
        assert msg.payload["status"] == 404
        assert msg.payload["error"] == "Model not found"
        assert msg.payload["code"] == "model_not_found"

    def test_create_ping_message(self):
        """Test create_ping_message factory."""
        msg = create_ping_message(correlation_id="ping-1")
        assert msg.type == MessageType.PING
        assert msg.id == "ping-1"
        assert msg.payload == {}

    def test_create_pong_message(self):
        """Test create_pong_message factory."""
        msg = create_pong_message(correlation_id="pong-1")
        assert msg.type == MessageType.PONG
        assert msg.id == "pong-1"

    def test_create_cancel_message(self):
        """Test create_cancel_message factory."""
        msg = create_cancel_message(correlation_id="cancel-1")
        assert msg.type == MessageType.CANCEL
        assert msg.id == "cancel-1"

    def test_create_pending_message(self):
        """Test create_pending_message factory."""
        msg = create_pending_message(
            correlation_id="pending-1",
            connector_id="conn-123",
            message="Please wait for approval",
        )
        assert msg.type == MessageType.PENDING
        assert msg.payload["connector_id"] == "conn-123"
        assert msg.payload["message"] == "Please wait for approval"

    def test_create_pending_message_default(self):
        """Test create_pending_message with default message."""
        msg = create_pending_message(correlation_id="pending-2", connector_id="conn-456")
        assert msg.payload["message"] == "Waiting for admin approval"

    def test_create_approved_message(self):
        """Test create_approved_message factory."""
        msg = create_approved_message(correlation_id="approved-1", api_key="ck-new-key")
        assert msg.type == MessageType.APPROVED
        assert msg.payload["api_key"] == "ck-new-key"

    def test_create_revoked_message(self):
        """Test create_revoked_message factory."""
        msg = create_revoked_message(correlation_id="revoked-1", reason="Abuse detected")
        assert msg.type == MessageType.REVOKED
        assert msg.payload["reason"] == "Abuse detected"

    def test_create_revoked_message_default(self):
        """Test create_revoked_message with default reason."""
        msg = create_revoked_message(correlation_id="revoked-2")
        assert msg.payload["reason"] == "API key revoked by admin"


class TestMessageRoundTrip:
    """Tests for message serialization/deserialization round trips."""

    def test_auth_message_roundtrip(self):
        """Test AUTH message survives serialization round trip."""
        original = create_auth_message(
            correlation_id="roundtrip-1",
            token="secret-token",
            models=["model-a", "model-b"],
        )
        json_str = original.model_dump_json()
        restored = RelayMessage.model_validate_json(json_str)

        assert restored.type == original.type
        assert restored.id == original.id
        assert restored.payload == original.payload

    def test_request_message_roundtrip(self):
        """Test REQUEST message survives serialization round trip."""
        original = create_request_message(
            correlation_id="roundtrip-2",
            method="POST",
            path="/v1/chat/completions",
            headers={"Content-Type": "application/json", "X-Custom": "value"},
            body="base64body==",
            llm_api_key="sk-key",
        )
        data = original.model_dump()
        restored = RelayMessage.model_validate(data)

        assert restored.payload["method"] == "POST"
        assert restored.payload["path"] == "/v1/chat/completions"
        assert restored.payload["headers"]["X-Custom"] == "value"
        assert restored.payload["llm_api_key"] == "sk-key"

    def test_error_message_roundtrip(self):
        """Test ERROR message survives serialization round trip."""
        original = create_error_message(
            correlation_id="roundtrip-3",
            status=503,
            error="Service unavailable",
            code="service_unavailable",
        )
        json_str = original.model_dump_json()
        restored = RelayMessage.model_validate_json(json_str)

        assert restored.payload["status"] == 503
        assert restored.payload["error"] == "Service unavailable"
