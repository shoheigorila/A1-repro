"""Base tool interface for A1 agent."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    """Result from tool execution."""

    summary: str  # LLM-friendly summary
    details: dict[str, Any] = field(default_factory=dict)  # Machine-readable data
    artifacts: list[str] = field(default_factory=list)  # File paths or references
    cache_key: str = ""
    success: bool = True
    error: str | None = None

    def to_prompt(self) -> str:
        """Format result for LLM prompt."""
        if not self.success:
            return f"Error: {self.error}"
        return self.summary


class Tool(ABC):
    """Abstract base class for all tools."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name for identification."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Tool description for LLM."""
        ...

    @property
    @abstractmethod
    def parameters_schema(self) -> dict[str, Any]:
        """JSON schema for tool parameters."""
        ...

    @abstractmethod
    async def execute(self, **kwargs: Any) -> ToolResult:
        """Execute the tool with given parameters."""
        ...

    def to_openai_tool(self) -> dict[str, Any]:
        """Convert to OpenAI function calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
            },
        }

    def to_anthropic_tool(self) -> dict[str, Any]:
        """Convert to Anthropic tool use format."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters_schema,
        }
