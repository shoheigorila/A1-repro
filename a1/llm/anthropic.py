"""Anthropic LLM client."""

import json
from typing import Any

from anthropic import AsyncAnthropic

from a1.config import llm_config
from a1.llm.client import LLMClient
from a1.llm.schema import (
    Message,
    LLMResponse,
    ToolDefinition,
    GenerationConfig,
    Role,
    ToolCall,
)


class AnthropicClient(LLMClient):
    """Anthropic API client."""

    def __init__(
        self,
        config: GenerationConfig,
        api_key: str | None = None,
    ):
        super().__init__(config)
        self.client = AsyncAnthropic(
            api_key=api_key or llm_config.anthropic_api_key,
        )

    async def generate(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> LLMResponse:
        """Generate a response from Anthropic."""
        # Extract system message
        system_content = ""
        chat_messages = []

        for msg in messages:
            if msg.role == Role.SYSTEM:
                system_content = msg.content or ""
            else:
                chat_messages.append(msg)

        # Convert messages to Anthropic format
        anthropic_messages = self._convert_messages(chat_messages)

        # Build request kwargs
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": anthropic_messages,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
            "top_p": self.config.top_p,
        }

        if system_content:
            kwargs["system"] = system_content

        if self.config.stop:
            kwargs["stop_sequences"] = self.config.stop

        if tools:
            kwargs["tools"] = [self._convert_tool(t) for t in tools]

        # Make request
        response = await self.client.messages.create(**kwargs)

        # Parse response
        content_text = ""
        tool_calls = []

        for block in response.content:
            if block.type == "text":
                content_text += block.text
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=block.input if isinstance(block.input, dict) else {},
                    )
                )

        return LLMResponse(
            message=Message(
                role=Role.ASSISTANT,
                content=content_text if content_text else None,
                tool_calls=tool_calls if tool_calls else None,
            ),
            finish_reason=response.stop_reason or "end_turn",
            usage={
                "prompt_tokens": response.usage.input_tokens,
                "completion_tokens": response.usage.output_tokens,
                "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
            },
        )

    def _convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """Convert messages to Anthropic format."""
        result = []

        for msg in messages:
            if msg.role == Role.USER:
                result.append({
                    "role": "user",
                    "content": msg.content or "",
                })
            elif msg.role == Role.ASSISTANT:
                content: list[dict[str, Any]] = []

                if msg.content:
                    content.append({"type": "text", "text": msg.content})

                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        content.append({
                            "type": "tool_use",
                            "id": tc.id,
                            "name": tc.name,
                            "input": tc.arguments,
                        })

                result.append({
                    "role": "assistant",
                    "content": content if content else msg.content or "",
                })
            elif msg.role == Role.TOOL:
                result.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.tool_call_id,
                            "content": msg.content or "",
                        }
                    ],
                })

        return result

    def _convert_tool(self, tool: ToolDefinition) -> dict[str, Any]:
        """Convert tool definition to Anthropic format."""
        return {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.parameters,
        }

    async def close(self) -> None:
        """Close the client."""
        await self.client.close()
