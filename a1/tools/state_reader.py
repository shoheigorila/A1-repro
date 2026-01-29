"""Blockchain State Reader tool."""

from typing import Any

from a1.chain.rpc import RPCClient
from a1.chain.abi import ABIManager, ERC20_ABI, UNISWAP_V2_PAIR_ABI
from a1.tools.base import Tool, ToolResult


# Common view functions to query
COMMON_QUERIES = [
    ("name", "name()", ["string"]),
    ("symbol", "symbol()", ["string"]),
    ("decimals", "decimals()", ["uint8"]),
    ("totalSupply", "totalSupply()", ["uint256"]),
    ("owner", "owner()", ["address"]),
]

PAIR_QUERIES = [
    ("token0", "token0()", ["address"]),
    ("token1", "token1()", ["address"]),
    ("getReserves", "getReserves()", ["uint112", "uint112", "uint32"]),
]


class BlockchainStateReader(Tool):
    """Read blockchain state from contracts."""

    def __init__(self, chain_id: int, rpc_url: str | None = None):
        self.chain_id = chain_id
        self.rpc = RPCClient(chain_id, rpc_url)
        self.abi_manager = ABIManager()

    @property
    def name(self) -> str:
        return "blockchain_state_reader"

    @property
    def description(self) -> str:
        return (
            "Read on-chain state from a smart contract. "
            "Can query common view functions (name, symbol, decimals, totalSupply, owner, balanceOf) "
            "or execute custom view function calls with ABI."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "address": {
                    "type": "string",
                    "description": "The contract address (0x...)",
                    "pattern": "^0x[a-fA-F0-9]{40}$",
                },
                "function": {
                    "type": "string",
                    "description": "Function signature (e.g., 'balanceOf(address)') or 'auto' for common queries",
                    "default": "auto",
                },
                "args": {
                    "type": "array",
                    "description": "Function arguments",
                    "items": {"type": "string"},
                    "default": [],
                },
                "block": {
                    "type": ["integer", "string"],
                    "description": "Block number or 'latest'",
                    "default": "latest",
                },
            },
            "required": ["address"],
        }

    async def execute(
        self,
        address: str,
        function: str = "auto",
        args: list[str] | None = None,
        block: int | str = "latest",
    ) -> ToolResult:
        """Read state from a contract."""
        try:
            args = args or []

            if function == "auto":
                return await self._query_common_state(address, block)

            # Execute specific function
            return await self._query_function(address, function, args, block)

        except Exception as e:
            return ToolResult(
                summary=f"Failed to read state: {str(e)}",
                success=False,
                error=str(e),
            )

    async def _query_common_state(
        self,
        address: str,
        block: int | str,
    ) -> ToolResult:
        """Query common state variables."""
        results: dict[str, Any] = {"address": address}

        # Check if contract has code
        code = await self.rpc.get_code(address, block)
        if code == "0x" or not code:
            return ToolResult(
                summary=f"Address {address} has no code (not a contract or self-destructed)",
                details={"address": address, "has_code": False},
                success=False,
                error="No code at address",
            )

        results["has_code"] = True

        # Try common ERC20 queries
        for name, sig, output_types in COMMON_QUERIES:
            try:
                data = self.abi_manager.encode_function_call(sig)
                result = await self.rpc.eth_call(address, data, block)
                if result and result != "0x":
                    decoded = self.abi_manager.decode_function_result(sig, result, output_types)
                    results[name] = decoded[0] if len(decoded) == 1 else decoded
            except Exception:
                pass

        # Try pair queries if looks like a pair
        is_pair = False
        for name, sig, output_types in PAIR_QUERIES:
            try:
                data = self.abi_manager.encode_function_call(sig)
                result = await self.rpc.eth_call(address, data, block)
                if result and result != "0x":
                    decoded = self.abi_manager.decode_function_result(sig, result, output_types)
                    if name == "getReserves":
                        results["reserve0"] = decoded[0]
                        results["reserve1"] = decoded[1]
                        results["blockTimestampLast"] = decoded[2]
                    else:
                        results[name] = decoded[0]
                    is_pair = True
            except Exception:
                pass

        results["is_pair"] = is_pair

        # Build summary
        summary_lines = [f"State for {address}:"]
        for key, value in results.items():
            if key not in ("address", "has_code", "is_pair"):
                if isinstance(value, int) and value > 10**12:
                    # Format large numbers
                    summary_lines.append(f"  {key}: {value:,}")
                else:
                    summary_lines.append(f"  {key}: {value}")

        if results.get("is_pair"):
            summary_lines.append("  (detected as Uniswap V2 Pair)")

        return ToolResult(
            summary="\n".join(summary_lines),
            details=results,
            cache_key=f"state:{self.chain_id}:{address.lower()}:{block}",
        )

    async def _query_function(
        self,
        address: str,
        function: str,
        args: list[str],
        block: int | str,
    ) -> ToolResult:
        """Query a specific function."""
        # Encode call
        data = self.abi_manager.encode_function_call(function, args)

        # Execute
        result = await self.rpc.eth_call(address, data, block)

        # Try to decode
        decoded = None
        try:
            decoded = self.abi_manager.decode_function_result(function, result)
        except Exception:
            pass

        return ToolResult(
            summary=f"{function} returned: {decoded if decoded else result}",
            details={
                "address": address,
                "function": function,
                "args": args,
                "raw_result": result,
                "decoded": decoded,
                "block": block,
            },
        )

    async def get_balance_of(
        self,
        token: str,
        account: str,
        block: int | str = "latest",
    ) -> int:
        """Get ERC20 balance of account."""
        sig = "balanceOf(address)"
        data = self.abi_manager.encode_function_call(sig, [account])
        result = await self.rpc.eth_call(token, data, block)
        decoded = self.abi_manager.decode_function_result(sig, result, ["uint256"])
        return decoded[0]

    async def close(self) -> None:
        """Close the RPC client."""
        await self.rpc.close()
