"""Main agent loop implementation."""

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from a1.config import settings
from a1.controller.prompt import PromptBuilder
from a1.controller.parser import StrategyParser
from a1.controller.policy import ToolPolicy, PolicyMode
from a1.llm.client import LLMClient
from a1.llm.schema import Message, Role, ToolCall, ToolDefinition
from a1.tools.base import Tool, ToolResult
from a1.tools.source_code import SourceCodeFetcher
from a1.tools.state_reader import BlockchainStateReader
from a1.tools.code_sanitizer import CodeSanitizer
from a1.tools.concrete_execution import ConcreteExecution


@dataclass
class TurnResult:
    """Result of a single turn."""
    turn: int
    messages: list[Message]
    tool_calls: list[dict[str, Any]]
    strategy_code: str | None
    execution_result: dict[str, Any] | None
    timestamp: float
    tokens_used: int


@dataclass
class LoopResult:
    """Result of the complete agent loop."""
    success: bool
    final_strategy: str | None
    final_profit: int
    turns: list[TurnResult]
    total_tokens: int
    total_tool_calls: int
    duration_seconds: float
    error: str | None = None


@dataclass
class AgentContext:
    """Context maintained across turns."""
    target_address: str
    chain_id: int
    block_number: int | None
    messages: list[Message] = field(default_factory=list)
    tool_results: dict[str, ToolResult] = field(default_factory=dict)
    strategies_tried: list[str] = field(default_factory=list)
    best_profit: int = 0
    best_strategy: str | None = None


class AgentLoop:
    """Main agent loop for exploit generation."""

    def __init__(
        self,
        llm_client: LLMClient,
        chain_id: int,
        rpc_url: str | None = None,
        max_turns: int | None = None,
        max_tool_calls: int | None = None,
    ):
        self.llm = llm_client
        self.chain_id = chain_id
        self.rpc_url = rpc_url
        self.max_turns = max_turns or settings.max_turns
        self.max_tool_calls = max_tool_calls or settings.max_tool_calls

        # Initialize components
        self.prompt_builder = PromptBuilder(chain_id)
        self.parser = StrategyParser()

        # Initialize tools
        self.tools: list[Tool] = [
            SourceCodeFetcher(chain_id),
            BlockchainStateReader(chain_id, rpc_url),
            CodeSanitizer(),
            ConcreteExecution(chain_id, rpc_url),
        ]

        self.policy = ToolPolicy(
            tools=self.tools,
            mode=PolicyMode.AGENT_CHOSEN,
            max_calls_per_turn=self.max_tool_calls,
        )

    async def run(
        self,
        target_address: str,
        block_number: int | None = None,
        additional_context: str = "",
    ) -> LoopResult:
        """Run the agent loop."""
        start_time = time.time()

        # Initialize context
        ctx = AgentContext(
            target_address=target_address,
            chain_id=self.chain_id,
            block_number=block_number,
        )

        # Build initial messages
        ctx.messages = [
            Message(role=Role.SYSTEM, content=self.prompt_builder.build_system_prompt()),
            Message(
                role=Role.USER,
                content=self.prompt_builder.build_initial_prompt(
                    target_address=target_address,
                    block_number=block_number,
                    additional_context=additional_context,
                ),
            ),
        ]

        turns: list[TurnResult] = []
        total_tokens = 0
        total_tool_calls = 0

        try:
            for turn in range(self.max_turns):
                turn_result = await self._run_turn(ctx, turn)
                turns.append(turn_result)
                total_tokens += turn_result.tokens_used
                total_tool_calls += len(turn_result.tool_calls)

                # Check for success
                if turn_result.execution_result:
                    exec_result = turn_result.execution_result
                    if exec_result.get("execution_success") and exec_result.get("profit", 0) > 0:
                        profit = exec_result["profit"]
                        if profit > ctx.best_profit:
                            ctx.best_profit = profit
                            ctx.best_strategy = turn_result.strategy_code

                        return LoopResult(
                            success=True,
                            final_strategy=ctx.best_strategy,
                            final_profit=ctx.best_profit,
                            turns=turns,
                            total_tokens=total_tokens,
                            total_tool_calls=total_tool_calls,
                            duration_seconds=time.time() - start_time,
                        )

            # Max turns reached without success
            return LoopResult(
                success=False,
                final_strategy=ctx.best_strategy,
                final_profit=ctx.best_profit,
                turns=turns,
                total_tokens=total_tokens,
                total_tool_calls=total_tool_calls,
                duration_seconds=time.time() - start_time,
                error="Max turns reached",
            )

        except Exception as e:
            return LoopResult(
                success=False,
                final_strategy=ctx.best_strategy,
                final_profit=ctx.best_profit,
                turns=turns,
                total_tokens=total_tokens,
                total_tool_calls=total_tool_calls,
                duration_seconds=time.time() - start_time,
                error=str(e),
            )

        finally:
            await self._cleanup()

    async def _run_turn(self, ctx: AgentContext, turn: int) -> TurnResult:
        """Run a single turn of the loop."""
        self.policy.reset_turn()
        tool_calls_made: list[dict[str, Any]] = []
        tokens_used = 0

        # Get LLM response
        tool_defs = self.policy.get_tool_definitions() if self.policy.should_use_tools(turn) else None

        response = await self.llm.generate(
            messages=ctx.messages,
            tools=tool_defs,
        )
        tokens_used += response.usage.get("total_tokens", 0)

        # Add assistant message to context
        ctx.messages.append(response.message)

        # Handle tool calls
        while response.message.tool_calls and self.policy.can_call_tool():
            for tc in response.message.tool_calls:
                result = await self.policy.execute_tool(tc.name, tc.arguments)

                tool_calls_made.append({
                    "tool": tc.name,
                    "arguments": tc.arguments,
                    "success": result.success,
                    "error": result.error,
                })

                # Add tool result to context
                ctx.messages.append(
                    Message(
                        role=Role.TOOL,
                        content=result.to_prompt(),
                        tool_call_id=tc.id,
                        name=tc.name,
                    )
                )

                # Cache result
                if result.cache_key:
                    ctx.tool_results[result.cache_key] = result

            # Get next response
            response = await self.llm.generate(
                messages=ctx.messages,
                tools=tool_defs,
            )
            tokens_used += response.usage.get("total_tokens", 0)
            ctx.messages.append(response.message)

        # Parse strategy from response
        strategy_code = None
        execution_result = None

        if response.message.content:
            parsed = self.parser.parse(response.message.content)
            if parsed:
                strategy_code = parsed.code

                # Validate and fix
                issues = self.parser.validate(parsed)
                if issues:
                    strategy_code = self.parser.fix_common_issues(strategy_code)

                # Execute strategy
                execution_tool = ConcreteExecution(self.chain_id, self.rpc_url)
                exec_result = await execution_tool.execute(
                    strategy_code=strategy_code,
                    block_number=ctx.block_number,
                )
                execution_result = exec_result.details

                tool_calls_made.append({
                    "tool": "concrete_execution",
                    "arguments": {"strategy_code": strategy_code[:100] + "..."},
                    "success": exec_result.success,
                    "error": exec_result.error,
                })

                # Add follow-up prompt
                follow_up = self.prompt_builder.build_follow_up_prompt(execution_result)
                ctx.messages.append(Message(role=Role.USER, content=follow_up))

                ctx.strategies_tried.append(strategy_code)

        return TurnResult(
            turn=turn,
            messages=ctx.messages.copy(),
            tool_calls=tool_calls_made,
            strategy_code=strategy_code,
            execution_result=execution_result,
            timestamp=time.time(),
            tokens_used=tokens_used,
        )

    async def _cleanup(self) -> None:
        """Cleanup resources."""
        for tool in self.tools:
            if hasattr(tool, "close"):
                await tool.close()
        await self.llm.close()


async def run_agent(
    target_address: str,
    chain_id: int = 1,
    block_number: int | None = None,
    model: str = "gpt-4-turbo",
    provider: str = "openai",
    rpc_url: str | None = None,
    max_turns: int = 5,
) -> LoopResult:
    """Convenience function to run the agent."""
    from a1.llm.schema import GenerationConfig

    # Create LLM client
    config = GenerationConfig(model=model, temperature=0.7, max_tokens=4096)

    if provider == "openai":
        from a1.llm.openai import OpenAIClient
        llm = OpenAIClient(config)
    elif provider == "anthropic":
        from a1.llm.anthropic import AnthropicClient
        llm = AnthropicClient(config)
    elif provider == "openrouter":
        from a1.llm.openrouter import OpenRouterClient
        llm = OpenRouterClient(config)
    else:
        raise ValueError(f"Unknown provider: {provider}")

    # Create and run agent
    agent = AgentLoop(
        llm_client=llm,
        chain_id=chain_id,
        rpc_url=rpc_url,
        max_turns=max_turns,
    )

    return await agent.run(
        target_address=target_address,
        block_number=block_number,
    )
