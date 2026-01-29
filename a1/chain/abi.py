"""ABI management and encoding utilities."""

import json
from pathlib import Path
from typing import Any

from eth_abi import encode, decode
from web3 import Web3


# Common function signatures
COMMON_SELECTORS = {
    "name()": "0x06fdde03",
    "symbol()": "0x95d89b41",
    "decimals()": "0x313ce567",
    "totalSupply()": "0x18160ddd",
    "balanceOf(address)": "0x70a08231",
    "owner()": "0x8da5cb5b",
    "getReserves()": "0x0902f1ac",
    "token0()": "0x0dfe1681",
    "token1()": "0xd21220a7",
    "factory()": "0xc45a0155",
    "WETH()": "0xad5c4648",
}

# Standard ABIs
ERC20_ABI = [
    {"constant": True, "inputs": [], "name": "name", "outputs": [{"type": "string"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "symbol", "outputs": [{"type": "string"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"type": "uint8"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "totalSupply", "outputs": [{"type": "uint256"}], "type": "function"},
    {"constant": True, "inputs": [{"name": "account", "type": "address"}], "name": "balanceOf", "outputs": [{"type": "uint256"}], "type": "function"},
    {"constant": True, "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}], "name": "allowance", "outputs": [{"type": "uint256"}], "type": "function"},
]

UNISWAP_V2_PAIR_ABI = [
    {"constant": True, "inputs": [], "name": "token0", "outputs": [{"type": "address"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "token1", "outputs": [{"type": "address"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "getReserves", "outputs": [{"type": "uint112"}, {"type": "uint112"}, {"type": "uint32"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "totalSupply", "outputs": [{"type": "uint256"}], "type": "function"},
]


class ABIManager:
    """Manages ABIs and encoding/decoding."""

    def __init__(self):
        self._abi_cache: dict[str, list[dict[str, Any]]] = {}
        self._selector_cache: dict[str, dict[str, Any]] = {}

    def get_function_selector(self, signature: str) -> str:
        """Get 4-byte function selector from signature."""
        if signature in COMMON_SELECTORS:
            return COMMON_SELECTORS[signature]
        return Web3.keccak(text=signature)[:4].hex()

    def encode_function_call(
        self,
        signature: str,
        args: list[Any] | None = None,
    ) -> str:
        """Encode a function call."""
        selector = self.get_function_selector(signature)

        if not args:
            return selector

        # Parse signature for types
        # e.g., "balanceOf(address)" -> ["address"]
        types_str = signature[signature.index("(") + 1 : signature.index(")")]
        if not types_str:
            return selector

        types = [t.strip() for t in types_str.split(",")]
        encoded_args = encode(types, args).hex()
        return selector + encoded_args

    def decode_function_result(
        self,
        signature: str,
        data: str,
        output_types: list[str] | None = None,
    ) -> tuple[Any, ...]:
        """Decode function return data."""
        if data == "0x" or not data:
            return ()

        # Remove 0x prefix
        if data.startswith("0x"):
            data = data[2:]

        if not output_types:
            # Try to infer from common signatures
            if "()string" in signature or signature.endswith("name()") or signature.endswith("symbol()"):
                output_types = ["string"]
            elif "()uint256" in signature or "Supply" in signature or "balanceOf" in signature:
                output_types = ["uint256"]
            elif "()uint8" in signature or "decimals" in signature:
                output_types = ["uint8"]
            elif "()address" in signature or "owner" in signature:
                output_types = ["address"]
            else:
                output_types = ["bytes"]

        return decode(output_types, bytes.fromhex(data))

    def get_view_functions(self, abi: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Extract view/pure functions from ABI."""
        return [
            item
            for item in abi
            if item.get("type") == "function"
            and item.get("stateMutability") in ("view", "pure")
        ]

    def get_function_signature(self, func: dict[str, Any]) -> str:
        """Get function signature from ABI entry."""
        name = func["name"]
        inputs = func.get("inputs", [])
        types = ",".join(inp["type"] for inp in inputs)
        return f"{name}({types})"

    def cache_abi(self, address: str, abi: list[dict[str, Any]]) -> None:
        """Cache an ABI for an address."""
        self._abi_cache[address.lower()] = abi

        # Build selector cache
        selector_map: dict[str, dict[str, Any]] = {}
        for item in abi:
            if item.get("type") == "function":
                sig = self.get_function_signature(item)
                selector = self.get_function_selector(sig)
                selector_map[selector] = item
        self._selector_cache[address.lower()] = selector_map

    def get_cached_abi(self, address: str) -> list[dict[str, Any]] | None:
        """Get cached ABI for address."""
        return self._abi_cache.get(address.lower())

    def decode_function_input(
        self,
        address: str,
        data: str,
    ) -> tuple[str, dict[str, Any]] | None:
        """Decode function input using cached ABI."""
        if len(data) < 10:
            return None

        selector = data[:10]
        selector_map = self._selector_cache.get(address.lower(), {})

        if selector not in selector_map:
            return None

        func = selector_map[selector]
        sig = self.get_function_signature(func)
        inputs = func.get("inputs", [])

        if not inputs:
            return (sig, {})

        # Decode arguments
        types = [inp["type"] for inp in inputs]
        names = [inp["name"] for inp in inputs]

        try:
            values = decode(types, bytes.fromhex(data[10:]))
            return (sig, dict(zip(names, values)))
        except Exception:
            return (sig, {})


# Global instance
abi_manager = ABIManager()
