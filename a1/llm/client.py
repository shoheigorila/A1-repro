"""Abstract LLM client interface."""

from abc import ABC, abstractmethod
from typing import Any

from a1.llm.schema import Message, LLMResponse, ToolDefinition, GenerationConfig, Role, ToolCall


class LLMClient(ABC):
    """Abstract base class for LLM clients."""

    def __init__(self, config: GenerationConfig):
        self.config = config

    @abstractmethod
    async def generate(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> LLMResponse:
        """Generate a response from the LLM."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Close the client."""
        ...

    def create_message(
        self,
        role: Role,
        content: str | None = None,
        tool_calls: list[ToolCall] | None = None,
        tool_call_id: str | None = None,
        name: str | None = None,
    ) -> Message:
        """Helper to create a message."""
        return Message(
            role=role,
            content=content,
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
            name=name,
        )

    def system_message(self, content: str) -> Message:
        """Create a system message."""
        return self.create_message(Role.SYSTEM, content)

    def user_message(self, content: str) -> Message:
        """Create a user message."""
        return self.create_message(Role.USER, content)

    def assistant_message(
        self,
        content: str | None = None,
        tool_calls: list[ToolCall] | None = None,
    ) -> Message:
        """Create an assistant message."""
        return self.create_message(Role.ASSISTANT, content, tool_calls)

    def tool_message(
        self,
        tool_call_id: str,
        name: str,
        content: str,
    ) -> Message:
        """Create a tool response message."""
        return self.create_message(
            Role.TOOL,
            content=content,
            tool_call_id=tool_call_id,
            name=name,
        )


# Re-export for convenience
__all__ = ["LLMClient", "Message", "ToolCall", "LLMResponse", "ToolDefinition", "GenerationConfig", "Role"]
