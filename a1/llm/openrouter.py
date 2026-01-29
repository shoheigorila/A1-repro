"""OpenRouter LLM client."""

import json
from typing import Any

import httpx

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


class OpenRouterClient(LLMClient):
    """OpenRouter API client (OpenAI-compatible)."""

    BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(
        self,
        config: GenerationConfig,
        api_key: str | None = None,
    ):
        super().__init__(config)
        self.api_key = api_key or llm_config.openrouter_api_key
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.BASE_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "HTTP-Referer": "https://github.com/a1-repro",
                    "X-Title": "A1-Repro",
                    "Content-Type": "application/json",
                },
                timeout=120.0,
            )
        return self._client

    async def generate(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> LLMResponse:
        """Generate a response from OpenRouter."""
        client = await self._get_client()

        # Convert messages to OpenAI format
        openai_messages = self._convert_messages(messages)

        # Build request body
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": openai_messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "top_p": self.config.top_p,
        }

        if self.config.stop:
            body["stop"] = self.config.stop

        if self.config.seed is not None:
            body["seed"] = self.config.seed

        if tools:
            body["tools"] = [self._convert_tool(t) for t in tools]
            body["tool_choice"] = "auto"

        # Make request
        response = await client.post("/chat/completions", json=body)
        response.raise_for_status()
        data = response.json()

        # Parse response
        choice = data["choices"][0]
        message = choice["message"]

        # Extract tool calls if present
        tool_calls = None
        if message.get("tool_calls"):
            tool_calls = [
                ToolCall(
                    id=tc["id"],
                    name=tc["function"]["name"],
                    arguments=json.loads(tc["function"]["arguments"]),
                )
                for tc in message["tool_calls"]
            ]

        usage = data.get("usage", {})

        return LLMResponse(
            message=Message(
                role=Role.ASSISTANT,
                content=message.get("content"),
                tool_calls=tool_calls,
            ),
            finish_reason=choice.get("finish_reason", "stop"),
            usage={
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
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
        if self._client:
            await self._client.aclose()
            self._client = None
