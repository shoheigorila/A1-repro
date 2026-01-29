"""Tool calling policy for agent."""

from enum import Enum
from typing import Any

from a1.tools.base import Tool, ToolResult
from a1.llm.schema import ToolDefinition


class PolicyMode(str, Enum):
    """Tool calling policy mode."""
    AGENT_CHOSEN = "agent_chosen"  # LLM decides which tools to call
    FIXED_SEQUENCE = "fixed_sequence"  # Predefined tool sequence
    HYBRID = "hybrid"  # Fixed initial sequence, then agent-chosen


class ToolPolicy:
    """Manages tool calling policy and execution."""

    def __init__(
        self,
        tools: list[Tool],
        mode: PolicyMode = PolicyMode.AGENT_CHOSEN,
        max_calls_per_turn: int = 5,
    ):
        self.tools = {t.name: t for t in tools}
        self.mode = mode
        self.max_calls_per_turn = max_calls_per_turn

        # For fixed sequence mode
        self.fixed_sequence: list[tuple[str, dict[str, Any]]] = []
        self.sequence_index = 0

        # Tracking
        self.calls_this_turn = 0
        self.total_calls = 0

    def get_tool_definitions(self) -> list[ToolDefinition]:
        """Get tool definitions for LLM."""
        return [
            ToolDefinition(
                name=tool.name,
                description=tool.description,
                parameters=tool.parameters_schema,
            )
            for tool in self.tools.values()
        ]

    def set_fixed_sequence(
        self,
        sequence: list[tuple[str, dict[str, Any]]],
    ) -> None:
        """Set a fixed tool calling sequence."""
        self.fixed_sequence = sequence
        self.sequence_index = 0
        self.mode = PolicyMode.FIXED_SEQUENCE

    def reset_turn(self) -> None:
        """Reset per-turn counters."""
        self.calls_this_turn = 0

    def can_call_tool(self) -> bool:
        """Check if more tool calls are allowed this turn."""
        return self.calls_this_turn < self.max_calls_per_turn

    def get_next_fixed_call(self) -> tuple[str, dict[str, Any]] | None:
        """Get next call in fixed sequence."""
        if self.sequence_index >= len(self.fixed_sequence):
            return None
        call = self.fixed_sequence[self.sequence_index]
        self.sequence_index += 1
        return call

    async def execute_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> ToolResult:
        """Execute a tool by name."""
        if name not in self.tools:
            return ToolResult(
                summary=f"Unknown tool: {name}",
                success=False,
                error=f"Tool '{name}' not found",
            )

        if not self.can_call_tool():
            return ToolResult(
                summary=f"Tool call limit reached ({self.max_calls_per_turn} per turn)",
                success=False,
                error="Call limit reached",
            )

        tool = self.tools[name]
        self.calls_this_turn += 1
        self.total_calls += 1

        try:
            result = await tool.execute(**arguments)
            return result
        except Exception as e:
            return ToolResult(
                summary=f"Tool execution failed: {str(e)}",
                success=False,
                error=str(e),
            )

    def should_use_tools(self, turn: int) -> bool:
        """Determine if tools should be offered this turn."""
        if self.mode == PolicyMode.AGENT_CHOSEN:
            return True
        elif self.mode == PolicyMode.FIXED_SEQUENCE:
            return self.sequence_index < len(self.fixed_sequence)
        elif self.mode == PolicyMode.HYBRID:
            # Fixed sequence for first few turns, then agent-chosen
            return True
        return True

    def get_tool_summary(self) -> str:
        """Get a summary of available tools."""
        lines = ["Available tools:"]
        for name, tool in self.tools.items():
            lines.append(f"  - {name}: {tool.description[:80]}...")
        return "\n".join(lines)
