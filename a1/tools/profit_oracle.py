"""Profit Oracle - Authoritative profit calculation with normalization."""

from dataclasses import dataclass, field
from typing import Any

from a1.config import get_chain_config
from a1.chain.rpc import RPCClient
from a1.tools.base import Tool, ToolResult
from a1.tools.dex_aggregator import DexAggregator


@dataclass
class TokenDelta:
    """Balance delta for a single token."""
    address: str
    symbol: str
    decimals: int
    before: int
    after: int
    delta: int
    value_in_base: int  # Estimated value in base token


@dataclass
class ProfitReport:
    """Complete profit analysis report."""
    # Input
    chain_id: int
    block_number: int | None
    base_token: str
    base_symbol: str

    # Token deltas
    token_deltas: list[TokenDelta] = field(default_factory=list)

    # Classification
    surplus_tokens: list[TokenDelta] = field(default_factory=list)
    deficit_tokens: list[TokenDelta] = field(default_factory=list)

    # Profit calculation (all in base token wei)
    base_token_delta: int = 0
    surplus_value: int = 0  # Value of surplus tokens if swapped to base
    deficit_cost: int = 0  # Cost to cover deficit tokens

    # Final profit
    raw_profit: int = 0  # Just base token delta
    gross_profit: int = 0  # base + surplus value
    net_profit: int = 0  # base + surplus - deficit cost

    # Validation
    is_profitable: bool = False
    all_balances_preserved: bool = True  # No token decreased
    confidence: float = 1.0  # 1.0 if all prices available, lower otherwise

    # Formatted outputs
    net_profit_formatted: float = 0.0
    net_profit_usd: float = 0.0  # If USD price available


class ProfitOracle(Tool):
    """Calculate authoritative profit from execution results."""

    def __init__(self, chain_id: int, rpc_url: str | None = None):
        self.chain_id = chain_id
        self.chain_info = get_chain_config(chain_id)
        self.base_token = self.chain_info["base_token"]
        self.base_symbol = self.chain_info["base_token_symbol"]
        self.rpc = RPCClient(chain_id, rpc_url)
        self.dex = DexAggregator(chain_id, rpc_url)

    @property
    def name(self) -> str:
        return "profit_oracle"

    @property
    def description(self) -> str:
        return (
            "Calculate authoritative profit from strategy execution. "
            "Handles multi-token balance changes, converts to base token value, "
            "and provides final profit determination."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "balance_changes": {
                    "type": "object",
                    "description": "Map of token address to {before, after} or just delta",
                },
                "block_number": {
                    "type": "integer",
                    "description": "Block number for price queries",
                },
            },
            "required": ["balance_changes"],
        }

    async def execute(
        self,
        balance_changes: dict[str, Any],
        block_number: int | None = None,
    ) -> ToolResult:
        """Calculate profit from balance changes."""
        try:
            report = await self.analyze(balance_changes, block_number)
            summary = self._build_summary(report)

            return ToolResult(
                summary=summary,
                details={
                    "chain_id": report.chain_id,
                    "base_token": report.base_token,
                    "base_token_delta": report.base_token_delta,
                    "surplus_value": report.surplus_value,
                    "deficit_cost": report.deficit_cost,
                    "raw_profit": report.raw_profit,
                    "gross_profit": report.gross_profit,
                    "net_profit": report.net_profit,
                    "is_profitable": report.is_profitable,
                    "all_balances_preserved": report.all_balances_preserved,
                    "confidence": report.confidence,
                    "net_profit_formatted": report.net_profit_formatted,
                    "token_deltas": [
                        {
                            "address": td.address,
                            "symbol": td.symbol,
                            "delta": td.delta,
                            "value_in_base": td.value_in_base,
                        }
                        for td in report.token_deltas
                    ],
                },
                success=True,
            )

        except Exception as e:
            return ToolResult(
                summary=f"Profit calculation failed: {str(e)}",
                success=False,
                error=str(e),
            )

    async def analyze(
        self,
        balance_changes: dict[str, Any],
        block_number: int | None = None,
    ) -> ProfitReport:
        """Perform complete profit analysis."""
        report = ProfitReport(
            chain_id=self.chain_id,
            block_number=block_number,
            base_token=self.base_token,
            base_symbol=self.base_symbol,
        )

        prices_unavailable = 0

        for token, change in balance_changes.items():
            # Parse change (can be int delta or {before, after} dict)
            if isinstance(change, dict):
                before = change.get("before", 0)
                after = change.get("after", 0)
                delta = after - before
            else:
                before = 0
                after = change
                delta = change

            # Get token info
            symbol, decimals = await self._get_token_info(token)

            # Calculate value in base token
            value_in_base = 0
            if delta != 0 and token.lower() != self.base_token.lower():
                try:
                    if delta > 0:
                        # Surplus: value if we swap to base
                        quote = await self.dex.get_quote(token, self.base_token, delta)
                        value_in_base = quote.amount_out
                    else:
                        # Deficit: cost to buy from base
                        quote = await self.dex.get_quote_exact_out(
                            self.base_token, token, abs(delta)
                        )
                        value_in_base = -quote.amount_in
                except Exception:
                    prices_unavailable += 1
                    # Conservative: surplus = 0, deficit = infinite
                    value_in_base = 0 if delta > 0 else -(2**128)
            elif token.lower() == self.base_token.lower():
                value_in_base = delta

            token_delta = TokenDelta(
                address=token,
                symbol=symbol,
                decimals=decimals,
                before=before,
                after=after,
                delta=delta,
                value_in_base=value_in_base,
            )

            report.token_deltas.append(token_delta)

            # Classify
            if token.lower() == self.base_token.lower():
                report.base_token_delta = delta
            elif delta > 0:
                report.surplus_tokens.append(token_delta)
                report.surplus_value += value_in_base
            elif delta < 0:
                report.deficit_tokens.append(token_delta)
                report.deficit_cost += abs(value_in_base)
                report.all_balances_preserved = False

        # Calculate profits
        report.raw_profit = report.base_token_delta
        report.gross_profit = report.base_token_delta + report.surplus_value
        report.net_profit = report.gross_profit - report.deficit_cost

        # Final determination
        report.is_profitable = report.net_profit > 0

        # Confidence based on price availability
        total_tokens = len(balance_changes)
        if total_tokens > 0:
            report.confidence = (total_tokens - prices_unavailable) / total_tokens

        # Formatted profit
        report.net_profit_formatted = report.net_profit / (10 ** 18)

        return report

    async def _get_token_info(self, token: str) -> tuple[str, int]:
        """Get token symbol and decimals."""
        if token == "0x" + "0" * 40:
            return ("ETH", 18)

        if token.lower() == self.base_token.lower():
            return (self.base_symbol, 18)

        symbol = token[:8] + "..."
        decimals = 18

        try:
            from a1.chain.abi import ABIManager
            abi = ABIManager()

            # Get symbol
            data = abi.encode_function_call("symbol()")
            result = await self.rpc.eth_call(token, data)
            if result and result != "0x":
                decoded = abi.decode_function_result("symbol()", result, ["string"])
                symbol = decoded[0]

            # Get decimals
            data = abi.encode_function_call("decimals()")
            result = await self.rpc.eth_call(token, data)
            if result and result != "0x":
                decoded = abi.decode_function_result("decimals()", result, ["uint8"])
                decimals = decoded[0]

        except Exception:
            pass

        return (symbol, decimals)

    def _build_summary(self, report: ProfitReport) -> str:
        """Build human-readable profit summary."""
        lines = [
            "# Profit Analysis Report",
            "",
            f"**Chain:** {report.chain_id}",
            f"**Base Token:** {report.base_symbol}",
            "",
        ]

        # Token deltas
        lines.append("## Token Balance Changes")
        for td in report.token_deltas:
            sign = "+" if td.delta >= 0 else ""
            value_str = f" (≈{td.value_in_base:+,} wei base)" if td.value_in_base != td.delta else ""
            lines.append(f"  {td.symbol}: {sign}{td.delta:,}{value_str}")

        lines.append("")

        # Profit breakdown
        lines.append("## Profit Breakdown")
        lines.append(f"  Base Token Delta: {report.base_token_delta:+,} wei")
        lines.append(f"  Surplus Value: {report.surplus_value:+,} wei")
        lines.append(f"  Deficit Cost: {report.deficit_cost:,} wei")
        lines.append("")
        lines.append(f"  **Raw Profit:** {report.raw_profit:+,} wei")
        lines.append(f"  **Gross Profit:** {report.gross_profit:+,} wei")
        lines.append(f"  **Net Profit:** {report.net_profit:+,} wei")
        lines.append(f"  **Formatted:** {report.net_profit_formatted:+.6f} {report.base_symbol}")

        lines.append("")

        # Verdict
        if report.is_profitable:
            lines.append("## ✅ PROFITABLE")
        else:
            lines.append("## ❌ NOT PROFITABLE")

        if not report.all_balances_preserved:
            lines.append("⚠️ Warning: Some token balances decreased")

        if report.confidence < 1.0:
            lines.append(f"⚠️ Price confidence: {report.confidence:.0%}")

        return "\n".join(lines)

    async def close(self) -> None:
        """Close resources."""
        await self.rpc.close()
        await self.dex.close()


async def calculate_profit(
    balance_changes: dict[str, Any],
    chain_id: int = 1,
    block_number: int | None = None,
) -> ProfitReport:
    """Convenience function to calculate profit."""
    oracle = ProfitOracle(chain_id)
    try:
        return await oracle.analyze(balance_changes, block_number)
    finally:
        await oracle.close()
