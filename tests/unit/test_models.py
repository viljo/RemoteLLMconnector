"""Unit tests for the OpenAI-compatible API models."""

import pytest
from pydantic import ValidationError

from remotellm.shared.models import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    Choice,
    DeltaMessage,
    ErrorDetail,
    ErrorResponse,
    Message,
    Model,
    ModelList,
    Role,
    StreamChoice,
    Usage,
)


class TestRole:
    """Tests for Role enum."""

    def test_role_values(self):
        """Test role enum values."""
        assert Role.SYSTEM == "system"
        assert Role.USER == "user"
        assert Role.ASSISTANT == "assistant"

    def test_role_is_string(self):
        """Test that roles are strings."""
        for role in Role:
            assert isinstance(role.value, str)


class TestMessage:
    """Tests for Message model."""

    def test_create_user_message(self):
        """Test creating a user message."""
        msg = Message(role=Role.USER, content="Hello, world!")
        assert msg.role == Role.USER
        assert msg.content == "Hello, world!"

    def test_create_system_message(self):
        """Test creating a system message."""
        msg = Message(role=Role.SYSTEM, content="You are a helpful assistant.")
        assert msg.role == Role.SYSTEM

    def test_create_assistant_message(self):
        """Test creating an assistant message."""
        msg = Message(role=Role.ASSISTANT, content="I can help with that.")
        assert msg.role == Role.ASSISTANT

    def test_message_serialization(self):
        """Test message serialization."""
        msg = Message(role=Role.USER, content="Test")
        data = msg.model_dump()
        assert data["role"] == "user"
        assert data["content"] == "Test"

    def test_message_from_dict(self):
        """Test creating message from dict."""
        data = {"role": "assistant", "content": "Response"}
        msg = Message.model_validate(data)
        assert msg.role == Role.ASSISTANT
        assert msg.content == "Response"

    def test_message_invalid_role(self):
        """Test that invalid role raises error."""
        with pytest.raises(ValidationError):
            Message(role="invalid", content="Test")

    def test_message_empty_content(self):
        """Test message with empty content is valid."""
        msg = Message(role=Role.USER, content="")
        assert msg.content == ""


class TestChatCompletionRequest:
    """Tests for ChatCompletionRequest model."""

    def test_minimal_request(self):
        """Test creating minimal request."""
        req = ChatCompletionRequest(
            model="gpt-4",
            messages=[Message(role=Role.USER, content="Hi")],
        )
        assert req.model == "gpt-4"
        assert len(req.messages) == 1

    def test_request_defaults(self):
        """Test request default values."""
        req = ChatCompletionRequest(
            model="llama3",
            messages=[Message(role=Role.USER, content="Test")],
        )
        assert req.temperature == 1.0
        assert req.top_p == 1.0
        assert req.max_tokens is None
        assert req.stream is False
        assert req.stop is None

    def test_request_with_all_options(self):
        """Test request with all options set."""
        req = ChatCompletionRequest(
            model="gpt-4",
            messages=[
                Message(role=Role.SYSTEM, content="Be helpful"),
                Message(role=Role.USER, content="Hello"),
            ],
            temperature=0.7,
            top_p=0.9,
            max_tokens=100,
            stream=True,
            stop=["END"],
        )
        assert req.temperature == 0.7
        assert req.top_p == 0.9
        assert req.max_tokens == 100
        assert req.stream is True
        assert req.stop == ["END"]

    def test_request_temperature_validation(self):
        """Test temperature must be between 0 and 2."""
        # Valid temperatures
        ChatCompletionRequest(
            model="test",
            messages=[Message(role=Role.USER, content="Test")],
            temperature=0,
        )
        ChatCompletionRequest(
            model="test",
            messages=[Message(role=Role.USER, content="Test")],
            temperature=2,
        )

        # Invalid temperatures
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="test",
                messages=[Message(role=Role.USER, content="Test")],
                temperature=-0.1,
            )
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="test",
                messages=[Message(role=Role.USER, content="Test")],
                temperature=2.1,
            )

    def test_request_top_p_validation(self):
        """Test top_p must be between 0 and 1."""
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="test",
                messages=[Message(role=Role.USER, content="Test")],
                top_p=1.5,
            )

    def test_request_max_tokens_validation(self):
        """Test max_tokens must be positive."""
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="test",
                messages=[Message(role=Role.USER, content="Test")],
                max_tokens=0,
            )

    def test_request_from_dict(self):
        """Test creating request from dict."""
        data = {
            "model": "claude-3",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True,
        }
        req = ChatCompletionRequest.model_validate(data)
        assert req.model == "claude-3"
        assert req.stream is True
        assert req.messages[0].role == Role.USER

    def test_request_stop_string(self):
        """Test stop can be a single string."""
        req = ChatCompletionRequest(
            model="test",
            messages=[Message(role=Role.USER, content="Test")],
            stop="STOP",
        )
        assert req.stop == "STOP"

    def test_request_stop_list(self):
        """Test stop can be a list of strings."""
        req = ChatCompletionRequest(
            model="test",
            messages=[Message(role=Role.USER, content="Test")],
            stop=["STOP", "END", "DONE"],
        )
        assert req.stop == ["STOP", "END", "DONE"]


class TestChoice:
    """Tests for Choice model."""

    def test_create_choice(self):
        """Test creating a choice."""
        choice = Choice(
            index=0,
            message=Message(role=Role.ASSISTANT, content="Hello!"),
            finish_reason="stop",
        )
        assert choice.index == 0
        assert choice.message.content == "Hello!"
        assert choice.finish_reason == "stop"

    def test_choice_finish_reasons(self):
        """Test valid finish reasons."""
        for reason in ["stop", "length", "content_filter"]:
            choice = Choice(
                index=0,
                message=Message(role=Role.ASSISTANT, content="Test"),
                finish_reason=reason,
            )
            assert choice.finish_reason == reason

    def test_choice_no_finish_reason(self):
        """Test choice without finish reason."""
        choice = Choice(
            index=0,
            message=Message(role=Role.ASSISTANT, content="Test"),
        )
        assert choice.finish_reason is None


class TestUsage:
    """Tests for Usage model."""

    def test_create_usage(self):
        """Test creating usage stats."""
        usage = Usage(prompt_tokens=10, completion_tokens=20, total_tokens=30)
        assert usage.prompt_tokens == 10
        assert usage.completion_tokens == 20
        assert usage.total_tokens == 30


class TestChatCompletionResponse:
    """Tests for ChatCompletionResponse model."""

    def test_create_response(self):
        """Test creating a completion response."""
        response = ChatCompletionResponse(
            id="chatcmpl-123",
            created=1699999999,
            model="gpt-4",
            choices=[
                Choice(
                    index=0,
                    message=Message(role=Role.ASSISTANT, content="Hello!"),
                    finish_reason="stop",
                )
            ],
            usage=Usage(prompt_tokens=5, completion_tokens=10, total_tokens=15),
        )
        assert response.id == "chatcmpl-123"
        assert response.object == "chat.completion"
        assert response.model == "gpt-4"
        assert len(response.choices) == 1
        assert response.usage.total_tokens == 15

    def test_response_defaults(self):
        """Test response default values."""
        response = ChatCompletionResponse(
            id="test-1",
            created=1700000000,
            model="test",
            choices=[],
        )
        assert response.object == "chat.completion"
        assert response.usage is None


class TestDeltaMessage:
    """Tests for DeltaMessage model."""

    def test_create_delta_with_role(self):
        """Test delta with role (first chunk)."""
        delta = DeltaMessage(role=Role.ASSISTANT)
        assert delta.role == Role.ASSISTANT
        assert delta.content is None

    def test_create_delta_with_content(self):
        """Test delta with content (subsequent chunks)."""
        delta = DeltaMessage(content="Hello")
        assert delta.role is None
        assert delta.content == "Hello"

    def test_create_empty_delta(self):
        """Test empty delta (final chunk)."""
        delta = DeltaMessage()
        assert delta.role is None
        assert delta.content is None


class TestStreamChoice:
    """Tests for StreamChoice model."""

    def test_create_stream_choice(self):
        """Test creating a stream choice."""
        choice = StreamChoice(
            index=0,
            delta=DeltaMessage(content="Hello"),
            finish_reason=None,
        )
        assert choice.index == 0
        assert choice.delta.content == "Hello"

    def test_stream_choice_final(self):
        """Test final stream choice with finish reason."""
        choice = StreamChoice(
            index=0,
            delta=DeltaMessage(),
            finish_reason="stop",
        )
        assert choice.finish_reason == "stop"


class TestChatCompletionChunk:
    """Tests for ChatCompletionChunk model."""

    def test_create_chunk(self):
        """Test creating a streaming chunk."""
        chunk = ChatCompletionChunk(
            id="chatcmpl-123",
            created=1700000000,
            model="gpt-4",
            choices=[
                StreamChoice(
                    index=0,
                    delta=DeltaMessage(content="Hello"),
                )
            ],
        )
        assert chunk.id == "chatcmpl-123"
        assert chunk.object == "chat.completion.chunk"
        assert chunk.choices[0].delta.content == "Hello"


class TestModel:
    """Tests for Model model."""

    def test_create_model(self):
        """Test creating a model."""
        model = Model(id="gpt-4")
        assert model.id == "gpt-4"
        assert model.object == "model"
        assert model.created == 0
        assert model.owned_by == "local"

    def test_create_model_full(self):
        """Test creating model with all fields."""
        model = Model(id="llama3", created=1700000000, owned_by="meta")
        assert model.owned_by == "meta"
        assert model.created == 1700000000


class TestModelList:
    """Tests for ModelList model."""

    def test_create_model_list(self):
        """Test creating a model list."""
        models = ModelList(
            data=[
                Model(id="gpt-4"),
                Model(id="llama3"),
            ]
        )
        assert models.object == "list"
        assert len(models.data) == 2
        assert models.data[0].id == "gpt-4"

    def test_empty_model_list(self):
        """Test empty model list."""
        models = ModelList(data=[])
        assert len(models.data) == 0


class TestErrorResponse:
    """Tests for error response models."""

    def test_error_detail(self):
        """Test ErrorDetail model."""
        error = ErrorDetail(
            message="Invalid request",
            type="invalid_request_error",
            code="invalid_model",
        )
        assert error.message == "Invalid request"
        assert error.type == "invalid_request_error"
        assert error.code == "invalid_model"

    def test_error_detail_no_code(self):
        """Test ErrorDetail without code."""
        error = ErrorDetail(message="Error", type="api_error")
        assert error.code is None

    def test_error_response(self):
        """Test ErrorResponse model."""
        response = ErrorResponse(
            error=ErrorDetail(message="Not found", type="not_found_error")
        )
        assert response.error.message == "Not found"

    def test_error_response_serialization(self):
        """Test error response serialization."""
        response = ErrorResponse(
            error=ErrorDetail(
                message="Model not found",
                type="invalid_request_error",
                code="model_not_found",
            )
        )
        data = response.model_dump()
        assert data["error"]["message"] == "Model not found"
        assert data["error"]["type"] == "invalid_request_error"
        assert data["error"]["code"] == "model_not_found"
