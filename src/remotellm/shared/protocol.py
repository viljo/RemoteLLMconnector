"""Tunnel protocol message types for connector-broker communication."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class MessageType(str, Enum):
    """Types of messages in the tunnel protocol."""

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


class TunnelMessage(BaseModel):
    """Base message format for tunnel protocol."""

    type: MessageType
    id: str = Field(description="Correlation ID for request tracking")
    payload: dict[str, Any] = Field(default_factory=dict)


# Auth messages
class AuthPayload(BaseModel):
    """Payload for AUTH message."""

    token: str
    connector_version: str = "1.0.0"
    models: list[str] = Field(default_factory=list, description="Models served by this connector")


class AuthOkPayload(BaseModel):
    """Payload for AUTH_OK message."""

    session_id: str


class AuthFailPayload(BaseModel):
    """Payload for AUTH_FAIL message."""

    error: str


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
    correlation_id: str, token: str, models: list[str] | None = None
) -> TunnelMessage:
    """Create an AUTH message."""
    payload = AuthPayload(token=token, models=models or [])
    return TunnelMessage(type=MessageType.AUTH, id=correlation_id, payload=payload.model_dump())


def create_auth_ok_message(correlation_id: str, session_id: str) -> TunnelMessage:
    """Create an AUTH_OK message."""
    payload = AuthOkPayload(session_id=session_id)
    return TunnelMessage(type=MessageType.AUTH_OK, id=correlation_id, payload=payload.model_dump())


def create_auth_fail_message(correlation_id: str, error: str) -> TunnelMessage:
    """Create an AUTH_FAIL message."""
    payload = AuthFailPayload(error=error)
    return TunnelMessage(
        type=MessageType.AUTH_FAIL, id=correlation_id, payload=payload.model_dump()
    )


def create_request_message(
    correlation_id: str,
    method: str,
    path: str,
    headers: dict[str, str],
    body: str = "",
    llm_api_key: str | None = None,
) -> TunnelMessage:
    """Create a REQUEST message."""
    payload = RequestPayload(
        method=method, path=path, headers=headers, body=body, llm_api_key=llm_api_key
    )
    return TunnelMessage(type=MessageType.REQUEST, id=correlation_id, payload=payload.model_dump())


def create_response_message(
    correlation_id: str, status: int, headers: dict[str, str], body: str = ""
) -> TunnelMessage:
    """Create a RESPONSE message."""
    payload = ResponsePayload(status=status, headers=headers, body=body)
    return TunnelMessage(type=MessageType.RESPONSE, id=correlation_id, payload=payload.model_dump())


def create_stream_chunk_message(
    correlation_id: str, chunk: str, done: bool = False
) -> TunnelMessage:
    """Create a STREAM_CHUNK message."""
    payload = StreamChunkPayload(chunk=chunk, done=done)
    return TunnelMessage(
        type=MessageType.STREAM_CHUNK, id=correlation_id, payload=payload.model_dump()
    )


def create_stream_end_message(correlation_id: str) -> TunnelMessage:
    """Create a STREAM_END message."""
    payload = StreamEndPayload()
    return TunnelMessage(
        type=MessageType.STREAM_END, id=correlation_id, payload=payload.model_dump()
    )


def create_error_message(correlation_id: str, status: int, error: str, code: str) -> TunnelMessage:
    """Create an ERROR message."""
    payload = ErrorPayload(status=status, error=error, code=code)
    return TunnelMessage(type=MessageType.ERROR, id=correlation_id, payload=payload.model_dump())


def create_ping_message(correlation_id: str) -> TunnelMessage:
    """Create a PING message."""
    return TunnelMessage(type=MessageType.PING, id=correlation_id, payload={})


def create_pong_message(correlation_id: str) -> TunnelMessage:
    """Create a PONG message."""
    return TunnelMessage(type=MessageType.PONG, id=correlation_id, payload={})


def create_cancel_message(correlation_id: str) -> TunnelMessage:
    """Create a CANCEL message."""
    return TunnelMessage(type=MessageType.CANCEL, id=correlation_id, payload={})
