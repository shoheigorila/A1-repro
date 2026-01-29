"""RPC client for blockchain interaction."""

import asyncio
from typing import Any

import httpx
from web3 import AsyncWeb3, AsyncHTTPProvider
from web3.types import BlockIdentifier

from a1.config import chain_config, get_chain_config


class RPCClient:
    """Async RPC client with retry and batching support."""

    def __init__(self, chain_id: int, rpc_url: str | None = None):
        self.chain_id = chain_id
        self.chain_info = get_chain_config(chain_id)

        # Determine RPC URL
        if rpc_url:
            self.rpc_url = rpc_url
        elif chain_id == 1:
            self.rpc_url = chain_config.eth_rpc_url
        elif chain_id == 56:
            self.rpc_url = chain_config.bsc_rpc_url
        else:
            raise ValueError(f"No RPC URL for chain {chain_id}")

        if not self.rpc_url:
            raise ValueError(f"RPC URL not configured for chain {chain_id}")

        self.w3 = AsyncWeb3(AsyncHTTPProvider(self.rpc_url))
        self._http_client: httpx.AsyncClient | None = None

    async def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    async def eth_call(
        self,
        to: str,
        data: str,
        block: BlockIdentifier = "latest",
    ) -> str:
        """Execute eth_call."""
        result = await self.w3.eth.call(
            {"to": to, "data": data},
            block,
        )
        return result.hex()

    async def get_code(self, address: str, block: BlockIdentifier = "latest") -> str:
        """Get contract bytecode."""
        code = await self.w3.eth.get_code(address, block)
        return code.hex()

    async def get_storage_at(
        self,
        address: str,
        slot: int,
        block: BlockIdentifier = "latest",
    ) -> str:
        """Read storage slot."""
        result = await self.w3.eth.get_storage_at(address, slot, block)
        return result.hex()

    async def get_block_number(self) -> int:
        """Get latest block number."""
        return await self.w3.eth.block_number

    async def batch_call(
        self,
        calls: list[tuple[str, str]],  # [(to, data), ...]
        block: BlockIdentifier = "latest",
    ) -> list[str]:
        """Execute multiple eth_calls in batch."""
        # Build JSON-RPC batch request
        client = await self._get_http_client()
        batch = [
            {
                "jsonrpc": "2.0",
                "method": "eth_call",
                "params": [{"to": to, "data": data}, block],
                "id": i,
            }
            for i, (to, data) in enumerate(calls)
        ]

        response = await client.post(
            self.rpc_url,
            json=batch,
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        results = response.json()

        # Sort by ID and extract results
        results.sort(key=lambda x: x["id"])
        return [r.get("result", "0x") for r in results]

    async def call_view_function(
        self,
        address: str,
        abi: list[dict[str, Any]],
        function_name: str,
        args: list[Any] | None = None,
        block: BlockIdentifier = "latest",
    ) -> Any:
        """Call a view function using ABI."""
        contract = self.w3.eth.contract(
            address=self.w3.to_checksum_address(address),
            abi=abi,
        )
        func = getattr(contract.functions, function_name)
        if args:
            return await func(*args).call(block_identifier=block)
        return await func().call(block_identifier=block)

    async def get_balance(self, address: str, block: BlockIdentifier = "latest") -> int:
        """Get ETH/native balance."""
        return await self.w3.eth.get_balance(
            self.w3.to_checksum_address(address),
            block,
        )
