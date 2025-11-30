"""Pydantic models for OpenAI-compatible API requests and responses."""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Role(str, Enum):
    """Message role in a chat conversation."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class Message(BaseModel):
    """A message in a chat conversation."""

    role: Role
    content: str


class ChatCompletionRequest(BaseModel):
    """Request body for /v1/chat/completions endpoint."""

    model: str = Field(description="ID of the model to use")
    messages: list[Message] = Field(description="List of messages in the conversation")
    temperature: float = Field(default=1.0, ge=0, le=2, description="Sampling temperature")
    top_p: float = Field(default=1.0, ge=0, le=1, description="Nucleus sampling parameter")
    max_tokens: int | None = Field(default=None, ge=1, description="Maximum tokens to generate")
    stream: bool = Field(default=False, description="Whether to stream the response")
    stop: str | list[str] | None = Field(default=None, description="Stop sequences")


class Choice(BaseModel):
    """A completion choice."""

    index: int
    message: Message
    finish_reason: Literal["stop", "length", "content_filter"] | None = None


class Usage(BaseModel):
    """Token usage information."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionResponse(BaseModel):
    """Response body for /v1/chat/completions endpoint (non-streaming)."""

    id: str = Field(description="Unique identifier for the completion")
    object: Literal["chat.completion"] = "chat.completion"
    created: int = Field(description="Unix timestamp of creation")
    model: str = Field(description="Model used for completion")
    choices: list[Choice]
    usage: Usage | None = None


class DeltaMessage(BaseModel):
    """Delta message for streaming responses."""

    role: Role | None = None
    content: str | None = None


class StreamChoice(BaseModel):
    """A streaming choice."""

    index: int
    delta: DeltaMessage
    finish_reason: Literal["stop", "length", "content_filter"] | None = None


class ChatCompletionChunk(BaseModel):
    """Streaming chunk for /v1/chat/completions endpoint."""

    id: str
    object: Literal["chat.completion.chunk"] = "chat.completion.chunk"
    created: int
    model: str
    choices: list[StreamChoice]


class Model(BaseModel):
    """A model available on the LLM server."""

    id: str = Field(description="Model identifier")
    object: Literal["model"] = "model"
    created: int = 0
    owned_by: str = "local"


class ModelList(BaseModel):
    """List of available models."""

    object: Literal["list"] = "list"
    data: list[Model]


class ErrorDetail(BaseModel):
    """Error detail object."""

    message: str = Field(description="Human-readable error message")
    type: str = Field(description="Error type")
    code: str | None = Field(default=None, description="Error code")


class ErrorResponse(BaseModel):
    """Error response body."""

    error: ErrorDetail
