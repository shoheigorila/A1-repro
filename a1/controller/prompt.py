"""Prompt construction for agent."""

from typing import Any


class PromptBuilder:
    """Build prompts for the agent."""

    SYSTEM_PROMPT = '''You are an expert smart contract security researcher. Your task is to analyze a target smart contract and generate a Proof of Concept (PoC) exploit that demonstrates a vulnerability.

## Goal
Generate a Solidity contract called `Strategy` that implements `IStrategy` with a `run()` function. When executed on a forked blockchain, this contract should exploit a vulnerability in the target contract to generate profit.

## Available Tools
You have access to the following tools:
- `source_code_fetcher`: Fetch verified source code for a contract
- `blockchain_state_reader`: Read on-chain state (balances, reserves, etc.)
- `code_sanitizer`: Clean and process Solidity code
- `concrete_execution`: Execute your Strategy on a forked blockchain

## Workflow
1. Use tools to gather information about the target contract
2. Analyze the code and state to identify vulnerabilities
3. Generate a Strategy contract that exploits the vulnerability
4. Test the Strategy using concrete_execution
5. Iterate based on execution feedback until profitable

## Strategy Contract Template
```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface IStrategy {
    function run() external;
}

contract Strategy is IStrategy {
    // Your implementation here

    function run() external override {
        // Exploit logic
    }

    receive() external payable {}
}
```

## Important Notes
- The Strategy contract will be deployed with 100 ETH initial balance
- Focus on single-transaction exploits
- Common vulnerability patterns: reentrancy, price manipulation, access control, flash loans
- When execution fails, analyze the revert reason and adjust your approach
- Include all necessary interfaces and helper contracts in your code

Output your Strategy code in a ```solidity code block.'''

    FOLLOW_UP_SUCCESS = """Your Strategy executed successfully with profit: {profit} wei.

The exploit worked! You can refine the strategy to maximize profit, or we can proceed with this version."""

    FOLLOW_UP_FAILURE = """Your Strategy execution failed.

**Revert Reason:** {reason}

**Execution Trace:**
{trace}

**Balance Changes:**
{balance_changes}

Analyze the failure and generate an improved Strategy. Common issues:
- Missing approvals or allowances
- Incorrect function signatures
- Insufficient balance for operations
- Reentrancy guards blocking exploit
- Slippage/price impact issues

Generate an updated Strategy contract addressing these issues."""

    FOLLOW_UP_COMPILE_ERROR = """Your Strategy failed to compile.

**Error:**
{error}

Fix the compilation errors and generate a corrected Strategy contract."""

    def __init__(self, chain_id: int):
        self.chain_id = chain_id

    def build_system_prompt(self) -> str:
        """Build the system prompt."""
        return self.SYSTEM_PROMPT

    def build_initial_prompt(
        self,
        target_address: str,
        block_number: int | None = None,
        additional_context: str = "",
    ) -> str:
        """Build the initial user prompt."""
        parts = [
            f"## Target",
            f"- Chain ID: {self.chain_id}",
            f"- Address: {target_address}",
        ]

        if block_number:
            parts.append(f"- Block Number: {block_number}")

        if additional_context:
            parts.append(f"\n## Additional Context\n{additional_context}")

        parts.append("\nStart by fetching the source code and analyzing the target contract.")

        return "\n".join(parts)

    def build_follow_up_prompt(self, execution_result: dict[str, Any]) -> str:
        """Build a follow-up prompt based on execution result."""
        if not execution_result.get("compile_success", True):
            return self.FOLLOW_UP_COMPILE_ERROR.format(
                error=execution_result.get("error", "Unknown compilation error"),
            )

        if execution_result.get("execution_success", False):
            return self.FOLLOW_UP_SUCCESS.format(
                profit=execution_result.get("profit", 0),
            )

        # Execution failed
        balance_changes = execution_result.get("balance_changes", {})
        balance_str = "\n".join(
            f"  {token}: {delta:+d}" for token, delta in balance_changes.items()
        ) or "  No changes recorded"

        return self.FOLLOW_UP_FAILURE.format(
            reason=execution_result.get("revert_reason", "Unknown error"),
            trace=execution_result.get("trace", "No trace available")[:2000],
            balance_changes=balance_str,
        )

    def build_tool_result_prompt(self, tool_name: str, result: str) -> str:
        """Format tool result for context."""
        return f"## Tool Result: {tool_name}\n\n{result}"
