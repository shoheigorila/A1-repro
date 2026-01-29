"""Block explorer API client (Etherscan/BSCScan)."""

from typing import Any

import httpx

from a1.config import chain_config, get_chain_config
from a1.tools.cache import cache


class ExplorerClient:
    """Client for Etherscan-compatible APIs."""

    def __init__(self, chain_id: int, api_key: str | None = None):
        self.chain_id = chain_id
        self.chain_info = get_chain_config(chain_id)
        self.base_url = self.chain_info["explorer_url"]

        # Determine API key
        if api_key:
            self.api_key = api_key
        elif chain_id == 1:
            self.api_key = chain_config.etherscan_api_key
        elif chain_id == 56:
            self.api_key = chain_config.bscscan_api_key
        else:
            self.api_key = ""

        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _request(
        self,
        module_or_params: str | dict[str, Any],
        action: str | None = None,
        extra_params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[Any]:
        """Make API request.

        Can be called as:
        - _request({"module": "...", "action": "...", ...})  # dict params
        - _request("module", "action", {"key": "value"})     # separate params
        """
        if isinstance(module_or_params, dict):
            params = module_or_params
        else:
            params = {
                "module": module_or_params,
                "action": action,
                **(extra_params or {}),
            }

        if self.api_key:
            params["apikey"] = self.api_key

        client = await self._get_client()
        response = await client.get(self.base_url, params=params)
        response.raise_for_status()
        data = response.json()

        if data.get("status") == "0" and data.get("message") != "No transactions found":
            raise Exception(f"Explorer API error: {data.get('result', 'Unknown error')}")

        return data.get("result", data)

    async def get_contract_source(self, address: str) -> dict[str, Any]:
        """Get verified contract source code."""
        # Check cache
        cache_key = cache.make_key("source", self.chain_id, address.lower())
        cached = cache.get(cache_key)
        if cached:
            return cached

        data = await self._request({
            "module": "contract",
            "action": "getsourcecode",
            "address": address,
        })

        result = data.get("result", [])
        if not result or not result[0].get("SourceCode"):
            return {
                "verified": False,
                "address": address,
                "error": "Contract not verified",
            }

        source_info = result[0]

        # Parse source code (may be JSON for multi-file)
        source_code = source_info.get("SourceCode", "")
        source_files: dict[str, str] = {}

        if source_code.startswith("{{"):
            # Multi-file JSON format (double-wrapped)
            import json
            try:
                # Remove outer braces and parse
                inner = source_code[1:-1]
                parsed = json.loads(inner)
                if "sources" in parsed:
                    for path, content in parsed["sources"].items():
                        source_files[path] = content.get("content", "")
                else:
                    for path, content in parsed.items():
                        source_files[path] = content.get("content", "")
            except json.JSONDecodeError:
                source_files["main.sol"] = source_code
        elif source_code.startswith("{"):
            # Single JSON object
            import json
            try:
                parsed = json.loads(source_code)
                if "sources" in parsed:
                    for path, content in parsed["sources"].items():
                        source_files[path] = content.get("content", "")
                else:
                    source_files["main.sol"] = source_code
            except json.JSONDecodeError:
                source_files["main.sol"] = source_code
        else:
            source_files["main.sol"] = source_code

        # Parse ABI
        abi_str = source_info.get("ABI", "[]")
        try:
            import json
            abi = json.loads(abi_str)
        except json.JSONDecodeError:
            abi = []

        result_data = {
            "verified": True,
            "address": address,
            "contract_name": source_info.get("ContractName", ""),
            "compiler_version": source_info.get("CompilerVersion", ""),
            "optimization_used": source_info.get("OptimizationUsed", "0") == "1",
            "runs": int(source_info.get("Runs", 200)),
            "source_files": source_files,
            "abi": abi,
            "constructor_arguments": source_info.get("ConstructorArguments", ""),
            "evm_version": source_info.get("EVMVersion", ""),
            "library": source_info.get("Library", ""),
            "proxy": source_info.get("Proxy", "0") == "1",
            "implementation": source_info.get("Implementation", ""),
        }

        # Cache result
        cache.set(cache_key, result_data)
        return result_data

    async def get_contract_abi(self, address: str) -> list[dict[str, Any]]:
        """Get contract ABI."""
        source = await self.get_contract_source(address)
        return source.get("abi", [])

    async def get_creation_tx(self, address: str) -> dict[str, Any] | None:
        """Get contract creation transaction."""
        cache_key = cache.make_key("creation_tx", self.chain_id, address.lower())
        cached = cache.get(cache_key)
        if cached:
            return cached

        data = await self._request({
            "module": "contract",
            "action": "getcontractcreation",
            "contractaddresses": address,
        })

        result = data.get("result", [])
        if not result:
            return None

        creation_info = result[0]
        result_data = {
            "creator": creation_info.get("contractCreator", ""),
            "tx_hash": creation_info.get("txHash", ""),
        }

        cache.set(cache_key, result_data)
        return result_data

    async def get_transactions(
        self,
        address: str,
        start_block: int = 0,
        end_block: int = 99999999,
        page: int = 1,
        offset: int = 100,
    ) -> list[dict[str, Any]]:
        """Get normal transactions for an address."""
        data = await self._request({
            "module": "account",
            "action": "txlist",
            "address": address,
            "startblock": start_block,
            "endblock": end_block,
            "page": page,
            "offset": offset,
            "sort": "desc",
        })
        return data.get("result", [])
