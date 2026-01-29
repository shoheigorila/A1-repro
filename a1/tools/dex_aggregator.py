"""DEX Aggregator for swap quotes and execution."""

from dataclasses import dataclass
from typing import Any

from a1.config import get_chain_config
from a1.chain.rpc import RPCClient
from a1.chain.abi import ABIManager
from a1.tools.base import Tool, ToolResult


@dataclass
class SwapQuote:
    """Quote for a token swap."""
    token_in: str
    token_out: str
    amount_in: int
    amount_out: int
    path: list[str]
    dex: str
    price_impact: float
    effective_price: float


@dataclass
class DexConfig:
    """Configuration for a DEX."""
    name: str
    router: str
    factory: str
    fee_bps: int = 30  # 0.3% default for Uniswap V2


# DEX configurations by chain
DEX_CONFIGS = {
    1: {  # Ethereum
        "uniswap_v2": DexConfig(
            name="Uniswap V2",
            router="0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",
            factory="0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",
            fee_bps=30,
        ),
        "sushiswap": DexConfig(
            name="SushiSwap",
            router="0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F",
            factory="0xC0AEe478e3658e2610c5F7A4A2E1777cE9e4f2Ac",
            fee_bps=30,
        ),
    },
    56: {  # BSC
        "pancakeswap_v2": DexConfig(
            name="PancakeSwap V2",
            router="0x10ED43C718714eb63d5aA57B78B54704E256024E",
            factory="0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73",
            fee_bps=25,
        ),
        "biswap": DexConfig(
            name="BiSwap",
            router="0x3a6d8cA21D1CF76F653A67577FA0D27453350dD8",
            factory="0x858E3312ed3A876947EA49d572A7C42DE08af7EE",
            fee_bps=10,
        ),
    },
}


class DexAggregator(Tool):
    """Aggregate quotes from multiple DEXes and find best swap paths."""

    # Slippage tolerance in basis points (5%)
    SLIPPAGE_BPS = 500
    MAX_HOPS = 3

    def __init__(self, chain_id: int, rpc_url: str | None = None):
        self.chain_id = chain_id
        self.chain_info = get_chain_config(chain_id)
        self.rpc = RPCClient(chain_id, rpc_url)
        self.abi_manager = ABIManager()

        # Get DEX configs for this chain
        self.dexes = DEX_CONFIGS.get(chain_id, {})

        # Intermediate tokens for multi-hop routing
        self.intermediates = [
            self.chain_info["base_token"],
            *self.chain_info.get("intermediates", []),
        ]

    @property
    def name(self) -> str:
        return "dex_aggregator"

    @property
    def description(self) -> str:
        return (
            "Get swap quotes from multiple DEXes to find the best rate. "
            "Supports direct swaps and multi-hop routing through intermediate tokens."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "token_in": {
                    "type": "string",
                    "description": "Input token address",
                },
                "token_out": {
                    "type": "string",
                    "description": "Output token address",
                },
                "amount_in": {
                    "type": "integer",
                    "description": "Amount of input token (in wei)",
                },
                "amount_out": {
                    "type": "integer",
                    "description": "Desired amount of output token (for exact output swaps)",
                },
            },
            "required": ["token_in", "token_out"],
        }

    async def execute(
        self,
        token_in: str,
        token_out: str,
        amount_in: int | None = None,
        amount_out: int | None = None,
    ) -> ToolResult:
        """Get best swap quote across DEXes."""
        try:
            if amount_in:
                quote = await self.get_quote(token_in, token_out, amount_in)
            elif amount_out:
                quote = await self.get_quote_exact_out(token_in, token_out, amount_out)
            else:
                return ToolResult(
                    summary="Either amount_in or amount_out must be specified",
                    success=False,
                    error="Missing amount",
                )

            summary = self._build_quote_summary(quote)

            return ToolResult(
                summary=summary,
                details={
                    "token_in": quote.token_in,
                    "token_out": quote.token_out,
                    "amount_in": quote.amount_in,
                    "amount_out": quote.amount_out,
                    "path": quote.path,
                    "dex": quote.dex,
                    "price_impact": quote.price_impact,
                    "effective_price": quote.effective_price,
                },
                success=True,
            )

        except Exception as e:
            return ToolResult(
                summary=f"Failed to get quote: {str(e)}",
                success=False,
                error=str(e),
            )

    async def get_quote(
        self,
        token_in: str,
        token_out: str,
        amount_in: int,
    ) -> SwapQuote:
        """Get best quote for exact input swap."""
        best_quote: SwapQuote | None = None

        for dex_name, dex_config in self.dexes.items():
            # Try direct path
            try:
                quote = await self._get_quote_from_dex(
                    dex_name=dex_name,
                    dex_config=dex_config,
                    token_in=token_in,
                    token_out=token_out,
                    amount_in=amount_in,
                    path=[token_in, token_out],
                )
                if quote and (not best_quote or quote.amount_out > best_quote.amount_out):
                    best_quote = quote
            except Exception:
                pass

            # Try 2-hop paths through intermediates
            for intermediate in self.intermediates:
                if intermediate.lower() in (token_in.lower(), token_out.lower()):
                    continue

                try:
                    quote = await self._get_quote_from_dex(
                        dex_name=dex_name,
                        dex_config=dex_config,
                        token_in=token_in,
                        token_out=token_out,
                        amount_in=amount_in,
                        path=[token_in, intermediate, token_out],
                    )
                    if quote and (not best_quote or quote.amount_out > best_quote.amount_out):
                        best_quote = quote
                except Exception:
                    pass

        if not best_quote:
            raise ValueError(f"No quote available for {token_in} -> {token_out}")

        return best_quote

    async def get_quote_exact_out(
        self,
        token_in: str,
        token_out: str,
        amount_out: int,
    ) -> SwapQuote:
        """Get best quote for exact output swap."""
        best_quote: SwapQuote | None = None

        for dex_name, dex_config in self.dexes.items():
            # Try direct path
            try:
                quote = await self._get_quote_exact_out_from_dex(
                    dex_name=dex_name,
                    dex_config=dex_config,
                    token_in=token_in,
                    token_out=token_out,
                    amount_out=amount_out,
                    path=[token_in, token_out],
                )
                if quote and (not best_quote or quote.amount_in < best_quote.amount_in):
                    best_quote = quote
            except Exception:
                pass

            # Try 2-hop paths
            for intermediate in self.intermediates:
                if intermediate.lower() in (token_in.lower(), token_out.lower()):
                    continue

                try:
                    quote = await self._get_quote_exact_out_from_dex(
                        dex_name=dex_name,
                        dex_config=dex_config,
                        token_in=token_in,
                        token_out=token_out,
                        amount_out=amount_out,
                        path=[token_in, intermediate, token_out],
                    )
                    if quote and (not best_quote or quote.amount_in < best_quote.amount_in):
                        best_quote = quote
                except Exception:
                    pass

        if not best_quote:
            raise ValueError(f"No quote available for {token_in} -> {token_out}")

        return best_quote

    async def _get_quote_from_dex(
        self,
        dex_name: str,
        dex_config: DexConfig,
        token_in: str,
        token_out: str,
        amount_in: int,
        path: list[str],
    ) -> SwapQuote | None:
        """Get quote from a specific DEX."""
        # Encode getAmountsOut call
        # getAmountsOut(uint256 amountIn, address[] memory path)
        sig = "getAmountsOut(uint256,address[])"

        # Manual encoding for array parameter
        from eth_abi import encode
        encoded_args = encode(
            ["uint256", "address[]"],
            [amount_in, [self.rpc.w3.to_checksum_address(p) for p in path]],
        )
        selector = self.abi_manager.get_function_selector(sig)
        data = selector + encoded_args.hex()

        result = await self.rpc.eth_call(dex_config.router, data)

        if not result or result == "0x":
            return None

        # Decode result
        from eth_abi import decode
        amounts = decode(["uint256[]"], bytes.fromhex(result[2:]))[0]

        if len(amounts) < 2:
            return None

        amount_out = amounts[-1]

        # Calculate price impact (simplified)
        price_impact = 0.0
        if amount_in > 0 and amount_out > 0:
            # Would need reserves for accurate calculation
            price_impact = dex_config.fee_bps / 10000 * len(path)

        return SwapQuote(
            token_in=token_in,
            token_out=token_out,
            amount_in=amount_in,
            amount_out=amount_out,
            path=path,
            dex=dex_name,
            price_impact=price_impact,
            effective_price=amount_out / amount_in if amount_in > 0 else 0,
        )

    async def _get_quote_exact_out_from_dex(
        self,
        dex_name: str,
        dex_config: DexConfig,
        token_in: str,
        token_out: str,
        amount_out: int,
        path: list[str],
    ) -> SwapQuote | None:
        """Get quote for exact output from a specific DEX."""
        # Encode getAmountsIn call
        sig = "getAmountsIn(uint256,address[])"

        from eth_abi import encode
        encoded_args = encode(
            ["uint256", "address[]"],
            [amount_out, [self.rpc.w3.to_checksum_address(p) for p in path]],
        )
        selector = self.abi_manager.get_function_selector(sig)
        data = selector + encoded_args.hex()

        result = await self.rpc.eth_call(dex_config.router, data)

        if not result or result == "0x":
            return None

        from eth_abi import decode
        amounts = decode(["uint256[]"], bytes.fromhex(result[2:]))[0]

        if len(amounts) < 2:
            return None

        amount_in = amounts[0]

        return SwapQuote(
            token_in=token_in,
            token_out=token_out,
            amount_in=amount_in,
            amount_out=amount_out,
            path=path,
            dex=dex_name,
            price_impact=dex_config.fee_bps / 10000 * len(path),
            effective_price=amount_out / amount_in if amount_in > 0 else 0,
        )

    async def get_pair_reserves(
        self,
        token_a: str,
        token_b: str,
        dex_name: str | None = None,
    ) -> dict[str, Any] | None:
        """Get reserves for a token pair."""
        dexes_to_check = (
            {dex_name: self.dexes[dex_name]} if dex_name else self.dexes
        )

        for name, config in dexes_to_check.items():
            try:
                # Get pair address from factory
                sig = "getPair(address,address)"
                from eth_abi import encode
                args = encode(
                    ["address", "address"],
                    [
                        self.rpc.w3.to_checksum_address(token_a),
                        self.rpc.w3.to_checksum_address(token_b),
                    ],
                )
                selector = self.abi_manager.get_function_selector(sig)
                data = selector + args.hex()

                result = await self.rpc.eth_call(config.factory, data)
                if not result or result == "0x":
                    continue

                from eth_abi import decode
                pair_address = decode(["address"], bytes.fromhex(result[2:]))[0]

                if pair_address == "0x" + "0" * 40:
                    continue

                # Get reserves
                reserves_sig = "getReserves()"
                reserves_data = self.abi_manager.get_function_selector(reserves_sig)
                reserves_result = await self.rpc.eth_call(pair_address, reserves_data)

                if not reserves_result or reserves_result == "0x":
                    continue

                reserves = decode(
                    ["uint112", "uint112", "uint32"],
                    bytes.fromhex(reserves_result[2:]),
                )

                # Get token order
                token0_sig = "token0()"
                token0_data = self.abi_manager.get_function_selector(token0_sig)
                token0_result = await self.rpc.eth_call(pair_address, token0_data)
                token0 = decode(["address"], bytes.fromhex(token0_result[2:]))[0]

                is_token_a_first = token0.lower() == token_a.lower()

                return {
                    "pair": pair_address,
                    "dex": name,
                    "token_a": token_a,
                    "token_b": token_b,
                    "reserve_a": reserves[0] if is_token_a_first else reserves[1],
                    "reserve_b": reserves[1] if is_token_a_first else reserves[0],
                    "block_timestamp": reserves[2],
                }

            except Exception:
                continue

        return None

    def _build_quote_summary(self, quote: SwapQuote) -> str:
        """Build human-readable quote summary."""
        return f"""## Swap Quote

**DEX:** {quote.dex}
**Path:** {' → '.join([a[:10] + '...' for a in quote.path])}

**Amount In:** {quote.amount_in}
**Amount Out:** {quote.amount_out}
**Effective Price:** {quote.effective_price:.8f}
**Price Impact:** {quote.price_impact:.2%}
"""

    async def close(self) -> None:
        """Close the RPC client."""
        await self.rpc.close()
