"""Revenue Normalizer tool for profit calculation."""

from dataclasses import dataclass
from typing import Any

from a1.config import get_chain_config
from a1.chain.rpc import RPCClient
from a1.chain.abi import ABIManager
from a1.tools.base import Tool, ToolResult


@dataclass
class BalanceChange:
    """Token balance change."""
    token: str
    symbol: str
    decimals: int
    before: int
    after: int
    delta: int
    delta_formatted: float


@dataclass
class NormalizationResult:
    """Result of revenue normalization."""
    # Raw balances
    balance_changes: list[BalanceChange]

    # Surplus/deficit classification
    surplus_tokens: list[tuple[str, int]]  # [(token, amount), ...]
    deficit_tokens: list[tuple[str, int]]

    # Profit calculation
    raw_profit_base: int  # Profit in base token before normalization
    normalized_profit_base: int  # After converting surplus/deficit

    # Swap details
    swaps_executed: list[dict[str, Any]]
    swap_costs: int  # Gas + slippage costs

    # Final verdict
    is_profitable: bool
    all_balances_non_negative: bool
    profit_formatted: float
    base_token_symbol: str


class RevenueNormalizer(Tool):
    """Normalize token balances to base token for profit calculation."""

    def __init__(self, chain_id: int, rpc_url: str | None = None):
        self.chain_id = chain_id
        self.chain_info = get_chain_config(chain_id)
        self.base_token = self.chain_info["base_token"]
        self.base_symbol = self.chain_info["base_token_symbol"]
        self.rpc = RPCClient(chain_id, rpc_url)
        self.abi_manager = ABIManager()

    @property
    def name(self) -> str:
        return "revenue_normalizer"

    @property
    def description(self) -> str:
        return (
            "Normalize token balance changes to base token for accurate profit calculation. "
            "Converts surplus tokens to base, estimates cost to cover deficits, "
            "and determines if the strategy is profitable."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "balance_changes": {
                    "type": "object",
                    "description": "Map of token address to balance delta",
                    "additionalProperties": {"type": "integer"},
                },
                "base_token": {
                    "type": "string",
                    "description": "Base token address (defaults to chain's native wrapped token)",
                },
            },
            "required": ["balance_changes"],
        }

    async def execute(
        self,
        balance_changes: dict[str, int],
        base_token: str | None = None,
    ) -> ToolResult:
        """Normalize balance changes and calculate profit."""
        try:
            base = base_token or self.base_token

            # Classify tokens
            surplus: list[tuple[str, int]] = []
            deficit: list[tuple[str, int]] = []
            base_delta = 0

            for token, delta in balance_changes.items():
                if token.lower() == base.lower() or token == "0x" + "0" * 40:
                    base_delta = delta
                elif delta > 0:
                    surplus.append((token, delta))
                elif delta < 0:
                    deficit.append((token, abs(delta)))

            # Calculate value of surplus in base token
            surplus_value = 0
            for token, amount in surplus:
                value = await self._get_token_value_in_base(token, amount, base)
                surplus_value += value

            # Calculate cost to cover deficit
            deficit_cost = 0
            for token, amount in deficit:
                cost = await self._get_cost_to_buy(token, amount, base)
                deficit_cost += cost

            # Net profit calculation
            raw_profit = base_delta
            normalized_profit = base_delta + surplus_value - deficit_cost

            # Check if all balances are non-negative
            all_non_negative = all(delta >= 0 for delta in balance_changes.values())

            # Get token info for display
            changes_info = await self._get_balance_changes_info(balance_changes)

            result = NormalizationResult(
                balance_changes=changes_info,
                surplus_tokens=surplus,
                deficit_tokens=deficit,
                raw_profit_base=raw_profit,
                normalized_profit_base=normalized_profit,
                swaps_executed=[],  # No actual swaps in estimation mode
                swap_costs=0,
                is_profitable=normalized_profit > 0,
                all_balances_non_negative=all_non_negative,
                profit_formatted=normalized_profit / 10**18,
                base_token_symbol=self.base_symbol,
            )

            # Build summary
            summary = self._build_summary(result)

            return ToolResult(
                summary=summary,
                details={
                    "raw_profit_base": raw_profit,
                    "normalized_profit_base": normalized_profit,
                    "surplus_value": surplus_value,
                    "deficit_cost": deficit_cost,
                    "is_profitable": result.is_profitable,
                    "all_non_negative": all_non_negative,
                    "surplus_tokens": surplus,
                    "deficit_tokens": deficit,
                    "profit_formatted": result.profit_formatted,
                },
                success=True,
            )

        except Exception as e:
            return ToolResult(
                summary=f"Failed to normalize revenue: {str(e)}",
                success=False,
                error=str(e),
            )

    async def _get_token_value_in_base(
        self,
        token: str,
        amount: int,
        base_token: str,
    ) -> int:
        """Get value of token amount in base token terms."""
        if amount == 0:
            return 0

        # Use DEX to get price quote
        from a1.tools.dex_aggregator import DexAggregator
        dex = DexAggregator(self.chain_id)

        try:
            quote = await dex.get_quote(
                token_in=token,
                token_out=base_token,
                amount_in=amount,
            )
            return quote.amount_out
        except Exception:
            # If no quote available, return 0 (conservative)
            return 0

    async def _get_cost_to_buy(
        self,
        token: str,
        amount: int,
        base_token: str,
    ) -> int:
        """Get cost in base token to buy token amount."""
        if amount == 0:
            return 0

        from a1.tools.dex_aggregator import DexAggregator
        dex = DexAggregator(self.chain_id)

        try:
            quote = await dex.get_quote(
                token_in=base_token,
                token_out=token,
                amount_out=amount,
            )
            return quote.amount_in
        except Exception:
            # If no quote, assume infinite cost
            return 2**255  # Very large number

    async def _get_balance_changes_info(
        self,
        balance_changes: dict[str, int],
    ) -> list[BalanceChange]:
        """Get detailed info for each balance change."""
        changes = []

        for token, delta in balance_changes.items():
            # Get token info
            symbol = "ETH"
            decimals = 18

            if token != "0x" + "0" * 40:
                try:
                    # Try to get symbol
                    data = self.abi_manager.encode_function_call("symbol()")
                    result = await self.rpc.eth_call(token, data)
                    if result and result != "0x":
                        decoded = self.abi_manager.decode_function_result("symbol()", result, ["string"])
                        symbol = decoded[0]

                    # Try to get decimals
                    data = self.abi_manager.encode_function_call("decimals()")
                    result = await self.rpc.eth_call(token, data)
                    if result and result != "0x":
                        decoded = self.abi_manager.decode_function_result("decimals()", result, ["uint8"])
                        decimals = decoded[0]
                except Exception:
                    symbol = token[:10] + "..."

            changes.append(BalanceChange(
                token=token,
                symbol=symbol,
                decimals=decimals,
                before=0,  # Not tracked in this context
                after=delta,  # Delta only
                delta=delta,
                delta_formatted=delta / (10 ** decimals),
            ))

        return changes

    def _build_summary(self, result: NormalizationResult) -> str:
        """Build human-readable summary."""
        lines = ["## Revenue Normalization Result\n"]

        # Balance changes
        lines.append("### Balance Changes")
        for bc in result.balance_changes:
            sign = "+" if bc.delta >= 0 else ""
            lines.append(f"  {bc.symbol}: {sign}{bc.delta_formatted:.6f}")

        lines.append("")

        # Surplus/Deficit
        if result.surplus_tokens:
            lines.append("### Surplus Tokens (to convert)")
            for token, amount in result.surplus_tokens:
                lines.append(f"  {token[:10]}...: {amount}")

        if result.deficit_tokens:
            lines.append("### Deficit Tokens (need to cover)")
            for token, amount in result.deficit_tokens:
                lines.append(f"  {token[:10]}...: -{amount}")

        lines.append("")

        # Profit
        lines.append("### Profit Analysis")
        lines.append(f"  Raw profit ({result.base_token_symbol}): {result.raw_profit_base}")
        lines.append(f"  Normalized profit: {result.normalized_profit_base}")
        lines.append(f"  Formatted: {result.profit_formatted:.6f} {result.base_token_symbol}")

        lines.append("")

        # Verdict
        if result.is_profitable:
            lines.append("**PROFITABLE** ✓")
        else:
            lines.append("**NOT PROFITABLE** ✗")

        if not result.all_balances_non_negative:
            lines.append("Warning: Some token balances decreased")

        return "\n".join(lines)

    async def close(self) -> None:
        """Close the RPC client."""
        await self.rpc.close()
