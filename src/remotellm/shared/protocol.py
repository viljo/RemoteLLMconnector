"""Relay protocol message types for connector-broker communication."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class MessageType(str, Enum):
    """Types of messages in the relay protocol."""

    # Connector → Broker
    AUTH = "AUTH"
    RESPONSE = "RESPONSE"
    STREAM_CHUNK = "STREAM_CHUNK"
    STREAM_END = "STREAM_END"
    ERROR = "ERROR"
    PING = "PING"

    # Broker → Connector
    AUTH_OK = "AUTH_OK"
    AUTH_FAIL = "AUTH_FAIL"
    REQUEST = "REQUEST"
    PONG = "PONG"
    CANCEL = "CANCEL"
    PENDING = "PENDING"  # Connector pending admin approval
    APPROVED = "APPROVED"  # Connector approved, here's your API key
    REVOKED = "REVOKED"  # Connector API key revoked


class RelayMessage(BaseModel):
    """Base message format for relay protocol."""

    type: MessageType
    id: str = Field(description="Correlation ID for request tracking")
    payload: dict[str, Any] = Field(default_factory=dict)


# Auth messages
class AuthPayload(BaseModel):
    """Payload for AUTH message."""

    token: str | None = Field(default=None, description="API key for approved connectors")
    name: str | None = Field(default=None, description="Optional friendly name for the connector")
    connector_version: str = "1.0.0"
    models: list[str] = Field(default_factory=list, description="Models served by this connector")


class AuthOkPayload(BaseModel):
    """Payload for AUTH_OK message."""

    session_id: str


class AuthFailPayload(BaseModel):
    """Payload for AUTH_FAIL message."""

    error: str


class PendingPayload(BaseModel):
    """Payload for PENDING message - connector awaiting admin approval."""

    connector_id: str = Field(description="Assigned connector ID")
    message: str = "Waiting for admin approval"


class ApprovedPayload(BaseModel):
    """Payload for APPROVED message - connector approved with API key."""

    api_key: str = Field(description="Generated API key for this connector")


class RevokedPayload(BaseModel):
    """Payload for REVOKED message - connector API key revoked."""

    reason: str = "API key revoked by admin"


# Request/Response messages
class RequestPayload(BaseModel):
    """Payload for REQUEST message from broker to connector."""

    method: str
    path: str
    headers: dict[str, str]
    body: str = ""  # Base64-encoded body
    llm_api_key: str | None = Field(default=None, description="LLM API key injected by broker")


class ResponsePayload(BaseModel):
    """Payload for RESPONSE message from connector to broker."""

    status: int
    headers: dict[str, str]
    body: str = ""  # Base64-encoded body


class StreamChunkPayload(BaseModel):
    """Payload for STREAM_CHUNK message."""

    chunk: str
    done: bool = False


class StreamEndPayload(BaseModel):
    """Payload for STREAM_END message."""

    done: bool = True


class ErrorPayload(BaseModel):
    """Payload for ERROR message."""

    status: int
    error: str
    code: str


# Helper functions
def create_auth_message(
    correlation_id: str,
    token: str | None = None,
    models: list[str] | None = None,
    name: str | None = None,
) -> RelayMessage:
    """Create an AUTH message."""
    payload = AuthPayload(token=token, name=name, models=models or [])
    return RelayMessage(type=MessageType.AUTH, id=correlation_id, payload=payload.model_dump())


def create_auth_ok_message(correlation_id: str, session_id: str) -> RelayMessage:
    """Create an AUTH_OK message."""
    payload = AuthOkPayload(session_id=session_id)
    return RelayMessage(type=MessageType.AUTH_OK, id=correlation_id, payload=payload.model_dump())


def create_auth_fail_message(correlation_id: str, error: str) -> RelayMessage:
    """Create an AUTH_FAIL message."""
    payload = AuthFailPayload(error=error)
    return RelayMessage(
        type=MessageType.AUTH_FAIL, id=correlation_id, payload=payload.model_dump()
    )


def create_request_message(
    correlation_id: str,
    method: str,
    path: str,
    headers: dict[str, str],
    body: str = "",
    llm_api_key: str | None = None,
) -> RelayMessage:
    """Create a REQUEST message."""
    payload = RequestPayload(
        method=method, path=path, headers=headers, body=body, llm_api_key=llm_api_key
    )
    return RelayMessage(type=MessageType.REQUEST, id=correlation_id, payload=payload.model_dump())


def create_response_message(
    correlation_id: str, status: int, headers: dict[str, str], body: str = ""
) -> RelayMessage:
    """Create a RESPONSE message."""
    payload = ResponsePayload(status=status, headers=headers, body=body)
    return RelayMessage(type=MessageType.RESPONSE, id=correlation_id, payload=payload.model_dump())


def create_stream_chunk_message(
    correlation_id: str, chunk: str, done: bool = False
) -> RelayMessage:
    """Create a STREAM_CHUNK message."""
    payload = StreamChunkPayload(chunk=chunk, done=done)
    return RelayMessage(
        type=MessageType.STREAM_CHUNK, id=correlation_id, payload=payload.model_dump()
    )


def create_stream_end_message(correlation_id: str) -> RelayMessage:
    """Create a STREAM_END message."""
    payload = StreamEndPayload()
    return RelayMessage(
        type=MessageType.STREAM_END, id=correlation_id, payload=payload.model_dump()
    )


def create_error_message(correlation_id: str, status: int, error: str, code: str) -> RelayMessage:
    """Create an ERROR message."""
    payload = ErrorPayload(status=status, error=error, code=code)
    return RelayMessage(type=MessageType.ERROR, id=correlation_id, payload=payload.model_dump())


def create_ping_message(correlation_id: str) -> RelayMessage:
    """Create a PING message."""
    return RelayMessage(type=MessageType.PING, id=correlation_id, payload={})


def create_pong_message(correlation_id: str) -> RelayMessage:
    """Create a PONG message."""
    return RelayMessage(type=MessageType.PONG, id=correlation_id, payload={})


def create_cancel_message(correlation_id: str) -> RelayMessage:
    """Create a CANCEL message."""
    return RelayMessage(type=MessageType.CANCEL, id=correlation_id, payload={})


def create_pending_message(correlation_id: str, connector_id: str, message: str | None = None) -> RelayMessage:
    """Create a PENDING message."""
    payload = PendingPayload(connector_id=connector_id, message=message or "Waiting for admin approval")
    return RelayMessage(type=MessageType.PENDING, id=correlation_id, payload=payload.model_dump())


def create_approved_message(correlation_id: str, api_key: str) -> RelayMessage:
    """Create an APPROVED message."""
    payload = ApprovedPayload(api_key=api_key)
    return RelayMessage(type=MessageType.APPROVED, id=correlation_id, payload=payload.model_dump())


def create_revoked_message(correlation_id: str, reason: str | None = None) -> RelayMessage:
    """Create a REVOKED message."""
    payload = RevokedPayload(reason=reason or "API key revoked by admin")
    return RelayMessage(type=MessageType.REVOKED, id=correlation_id, payload=payload.model_dump())
