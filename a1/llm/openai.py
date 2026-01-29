"""OpenAI LLM client."""

import json
from typing import Any

from openai import AsyncOpenAI

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


class OpenAIClient(LLMClient):
    """OpenAI API client."""

    def __init__(
        self,
        config: GenerationConfig,
        api_key: str | None = None,
    ):
        super().__init__(config)
        self.client = AsyncOpenAI(
            api_key=api_key or llm_config.openai_api_key,
        )

    async def generate(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> LLMResponse:
        """Generate a response from OpenAI."""
        # Convert messages to OpenAI format
        openai_messages = self._convert_messages(messages)

        # Build request kwargs
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": openai_messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "top_p": self.config.top_p,
        }

        if self.config.stop:
            kwargs["stop"] = self.config.stop

        if self.config.seed is not None:
            kwargs["seed"] = self.config.seed

        if tools:
            kwargs["tools"] = [self._convert_tool(t) for t in tools]
            kwargs["tool_choice"] = "auto"

        # Make request
        response = await self.client.chat.completions.create(**kwargs)

        # Parse response
        choice = response.choices[0]
        message = choice.message

        # Extract tool calls if present
        tool_calls = None
        if message.tool_calls:
            tool_calls = [
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=json.loads(tc.function.arguments),
                )
                for tc in message.tool_calls
            ]

        return LLMResponse(
            message=Message(
                role=Role.ASSISTANT,
                content=message.content,
                tool_calls=tool_calls,
            ),
            finish_reason=choice.finish_reason or "stop",
            usage={
                "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                "total_tokens": response.usage.total_tokens if response.usage else 0,
            },
        )

    def _convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """Convert messages to OpenAI format."""
        result = []
        for msg in messages:
            if msg.role == Role.TOOL:
                result.append({
                    "role": "tool",
                    "content": msg.content or "",
                    "tool_call_id": msg.tool_call_id,
                })
            elif msg.role == Role.ASSISTANT and msg.tool_calls:
                result.append({
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments),
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                })
            else:
                result.append({
                    "role": msg.role.value,
                    "content": msg.content or "",
                })
        return result

    def _convert_tool(self, tool: ToolDefinition) -> dict[str, Any]:
        """Convert tool definition to OpenAI format."""
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }

    async def close(self) -> None:
        """Close the client."""
        await self.client.close()
