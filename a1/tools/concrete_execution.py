"""Concrete Execution tool using Forge."""

import asyncio
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from a1.config import settings, get_chain_config
from a1.tools.base import Tool, ToolResult


class ConcreteExecution(Tool):
    """Execute Solidity code on forked blockchain using Forge."""

    def __init__(self, chain_id: int, rpc_url: str | None = None):
        self.chain_id = chain_id
        self.chain_info = get_chain_config(chain_id)

        # Get RPC URL
        if rpc_url:
            self.rpc_url = rpc_url
        else:
            from a1.config import chain_config
            if chain_id == 1:
                self.rpc_url = chain_config.eth_rpc_url
            elif chain_id == 56:
                self.rpc_url = chain_config.bsc_rpc_url
            else:
                raise ValueError(f"No RPC URL for chain {chain_id}")

        self.forge_bin = settings.forge_bin
        self.timeout = settings.execution_timeout

    @property
    def name(self) -> str:
        return "concrete_execution"

    @property
    def description(self) -> str:
        return (
            "Execute a Solidity strategy contract on a forked blockchain. "
            "Returns compilation status, execution trace, revert reasons, gas usage, "
            "and balance changes. The strategy must implement IStrategy with a run() function."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "strategy_code": {
                    "type": "string",
                    "description": "Solidity code for the strategy contract",
                },
                "block_number": {
                    "type": "integer",
                    "description": "Block number to fork from (optional, defaults to latest)",
                },
                "tracked_tokens": {
                    "type": "array",
                    "description": "Token addresses to track balance changes",
                    "items": {"type": "string"},
                    "default": [],
                },
                "initial_balance": {
                    "type": "string",
                    "description": "Initial ETH balance for the harness (in wei)",
                    "default": "100000000000000000000",  # 100 ETH
                },
            },
            "required": ["strategy_code"],
        }

    async def execute(
        self,
        strategy_code: str,
        block_number: int | None = None,
        tracked_tokens: list[str] | None = None,
        initial_balance: str = "100000000000000000000",
    ) -> ToolResult:
        """Execute strategy on forked blockchain."""
        try:
            # Create temporary directory for execution
            with tempfile.TemporaryDirectory(prefix="a1_exec_") as tmpdir:
                result = await self._run_forge(
                    tmpdir=tmpdir,
                    strategy_code=strategy_code,
                    block_number=block_number,
                    tracked_tokens=tracked_tokens or [],
                    initial_balance=initial_balance,
                )
                return result

        except asyncio.TimeoutError:
            return ToolResult(
                summary=f"Execution timed out after {self.timeout} seconds",
                success=False,
                error="Timeout",
            )
        except Exception as e:
            return ToolResult(
                summary=f"Execution failed: {str(e)}",
                success=False,
                error=str(e),
            )

    async def _run_forge(
        self,
        tmpdir: str,
        strategy_code: str,
        block_number: int | None,
        tracked_tokens: list[str],
        initial_balance: str,
    ) -> ToolResult:
        """Run Forge test in temporary directory."""
        tmppath = Path(tmpdir)

        # Initialize Forge project structure
        (tmppath / "src").mkdir()
        (tmppath / "test").mkdir()
        (tmppath / "lib").mkdir()

        # Copy forge-std from harness
        harness_lib = Path(__file__).parent.parent.parent / "harness" / "lib" / "forge-std"
        if harness_lib.exists():
            shutil.copytree(harness_lib, tmppath / "lib" / "forge-std")
        else:
            # Try to find it elsewhere or error
            return ToolResult(
                summary="forge-std not found. Run 'forge install' in harness directory.",
                success=False,
                error="forge-std not found",
            )

        # Write foundry.toml
        foundry_toml = f"""
[profile.default]
src = "src"
out = "out"
libs = ["lib"]
solc = "0.8.20"
evm_version = "paris"
"""
        (tmppath / "foundry.toml").write_text(foundry_toml)

        # Write strategy code
        (tmppath / "src" / "Strategy.sol").write_text(strategy_code)

        # Generate test file
        test_code = self._generate_test(
            tracked_tokens=tracked_tokens,
            initial_balance=initial_balance,
        )
        (tmppath / "test" / "Execute.t.sol").write_text(test_code)

        # Build fork args
        fork_args = [f"--fork-url={self.rpc_url}"]
        if block_number:
            fork_args.append(f"--fork-block-number={block_number}")

        # Run forge test
        cmd = [
            self.forge_bin,
            "test",
            *fork_args,
            "-vvvvv",
            "--json",
            "--match-test=test_Execute",
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=tmppath,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "FOUNDRY_PROFILE": "default"},
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError:
            process.kill()
            raise

        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")

        # Parse result
        return self._parse_result(
            returncode=process.returncode or 0,
            stdout=stdout_text,
            stderr=stderr_text,
            strategy_code=strategy_code,
        )

    def _generate_test(
        self,
        tracked_tokens: list[str],
        initial_balance: str,
    ) -> str:
        """Generate test file for strategy execution."""
        base_token = self.chain_info["base_token"]

        tokens_array = ", ".join([f'address({t})' for t in tracked_tokens])
        if tokens_array:
            tokens_array = f"[{tokens_array}]"
        else:
            tokens_array = "new address[](0)"

        return f'''// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "../src/Strategy.sol";

interface IERC20 {{
    function balanceOf(address) external view returns (uint256);
}}

contract ExecuteTest is Test {{
    address constant BASE_TOKEN = {base_token};

    Strategy public strategy;

    address[] public trackedTokens;
    mapping(address => uint256) public balancesBefore;

    event BalanceChange(address token, int256 delta);
    event ExecutionResult(bool success, string reason, int256 profit);

    function setUp() public {{
        // Deploy strategy
        strategy = new Strategy();

        // Fund strategy
        vm.deal(address(strategy), {initial_balance});

        // Setup tracked tokens
        address[] memory tokens = {tokens_array};
        for (uint i = 0; i < tokens.length; i++) {{
            trackedTokens.push(tokens[i]);
        }}
        trackedTokens.push(BASE_TOKEN);
    }}

    function test_Execute() public {{
        // Snapshot balances before
        for (uint i = 0; i < trackedTokens.length; i++) {{
            address token = trackedTokens[i];
            balancesBefore[token] = IERC20(token).balanceOf(address(strategy));
        }}
        uint256 ethBefore = address(strategy).balance;

        // Execute strategy
        bool success;
        string memory reason;
        try strategy.run() {{
            success = true;
        }} catch Error(string memory r) {{
            success = false;
            reason = r;
        }} catch {{
            success = false;
            reason = "Unknown error";
        }}

        // Calculate balance changes
        int256 totalProfit = 0;
        for (uint i = 0; i < trackedTokens.length; i++) {{
            address token = trackedTokens[i];
            uint256 balanceAfter = IERC20(token).balanceOf(address(strategy));
            int256 delta = int256(balanceAfter) - int256(balancesBefore[token]);
            emit BalanceChange(token, delta);

            if (token == BASE_TOKEN) {{
                totalProfit += delta;
            }}
        }}

        // ETH balance change
        int256 ethDelta = int256(address(strategy).balance) - int256(ethBefore);
        emit BalanceChange(address(0), ethDelta);

        emit ExecutionResult(success, reason, totalProfit);

        // Log results
        if (success) {{
            console.log("Execution: SUCCESS");
        }} else {{
            console.log("Execution: FAILED");
            console.log("Reason:", reason);
        }}
        console.log("Profit (base token):");
        console.logInt(totalProfit);
    }}
}}
'''

    def _parse_result(
        self,
        returncode: int,
        stdout: str,
        stderr: str,
        strategy_code: str,
    ) -> ToolResult:
        """Parse Forge output."""
        details: dict[str, Any] = {
            "returncode": returncode,
            "strategy_code": strategy_code,
        }

        # Check for compilation errors
        if "Compiler run failed" in stderr or "Error:" in stderr:
            # Extract error message
            error_match = re.search(r"Error[:\s]+(.+?)(?:\n|$)", stderr)
            error_msg = error_match.group(1) if error_match else "Compilation failed"

            return ToolResult(
                summary=f"Compilation failed: {error_msg}\n\nFull output:\n{stderr[:2000]}",
                details={
                    **details,
                    "compile_success": False,
                    "error": error_msg,
                    "stderr": stderr,
                },
                success=False,
                error=error_msg,
            )

        details["compile_success"] = True

        # Try to parse JSON output
        json_match = re.search(r'\{.*"test_results".*\}', stdout, re.DOTALL)
        if json_match:
            try:
                json_data = json.loads(json_match.group())
                details["json_output"] = json_data
            except json.JSONDecodeError:
                pass

        # Parse execution result from logs
        execution_success = returncode == 0
        revert_reason = ""
        profit = 0

        # Look for execution result in output
        if "Execution: SUCCESS" in stdout:
            execution_success = True
        elif "Execution: FAILED" in stdout:
            execution_success = False
            reason_match = re.search(r"Reason:\s*(.+)", stdout)
            if reason_match:
                revert_reason = reason_match.group(1).strip()

        # Look for profit
        profit_match = re.search(r"Profit.*?:\s*(-?\d+)", stdout)
        if profit_match:
            profit = int(profit_match.group(1))

        # Parse balance changes from events
        balance_changes: dict[str, int] = {}
        for match in re.finditer(r"BalanceChange\(([^,]+),\s*(-?\d+)\)", stdout):
            token = match.group(1)
            delta = int(match.group(2))
            balance_changes[token] = delta

        # Extract gas usage
        gas_match = re.search(r"gas:\s*(\d+)", stdout)
        gas_used = int(gas_match.group(1)) if gas_match else 0

        # Parse trace (simplified)
        trace_lines = []
        in_trace = False
        for line in stdout.splitlines():
            if "Traces:" in line:
                in_trace = True
            elif in_trace:
                if line.strip() and not line.startswith("Suite"):
                    trace_lines.append(line)
                if "Suite result:" in line:
                    break

        details.update({
            "execution_success": execution_success,
            "revert_reason": revert_reason,
            "profit": profit,
            "gas_used": gas_used,
            "balance_changes": balance_changes,
            "trace": "\n".join(trace_lines[:100]),  # Limit trace length
        })

        # Build summary
        if execution_success:
            summary = f"Execution SUCCESS\nProfit: {profit} wei\nGas: {gas_used}"
        else:
            summary = f"Execution FAILED\nReason: {revert_reason or 'Unknown'}\nGas: {gas_used}"

        if balance_changes:
            summary += "\n\nBalance Changes:"
            for token, delta in balance_changes.items():
                summary += f"\n  {token}: {delta:+d}"

        if trace_lines:
            summary += f"\n\nTrace (first 20 lines):\n" + "\n".join(trace_lines[:20])

        return ToolResult(
            summary=summary,
            details=details,
            success=execution_success,
            error=revert_reason if not execution_success else None,
        )
