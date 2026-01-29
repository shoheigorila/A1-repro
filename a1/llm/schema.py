"""Pydantic schemas for LLM interactions."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Role(str, Enum):
    """Message role."""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolCall(BaseModel):
    """Tool call from LLM."""
    id: str
    name: str
    arguments: dict[str, Any]


class Message(BaseModel):
    """Chat message."""
    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None  # For tool responses
    name: str | None = None  # Tool name for tool responses


class LLMResponse(BaseModel):
    """Response from LLM."""
    message: Message
    finish_reason: str
    usage: dict[str, int] = Field(default_factory=dict)


class ToolDefinition(BaseModel):
    """Tool definition for LLM."""
    name: str
    description: str
    parameters: dict[str, Any]


class GenerationConfig(BaseModel):
    """Generation configuration."""
    model: str
    temperature: float = 0.7
    max_tokens: int = 4096
    top_p: float = 1.0
    stop: list[str] | None = None
    seed: int | None = None
