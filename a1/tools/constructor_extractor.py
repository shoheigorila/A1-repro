"""Constructor Parameter Extractor - Extract and decode constructor arguments."""

from dataclasses import dataclass, field
from typing import Any

from eth_abi import decode
from eth_abi.exceptions import DecodingError

from a1.chain.rpc import RPCClient
from a1.chain.explorer import ExplorerClient
from a1.tools.base import Tool, ToolResult


@dataclass
class ConstructorParam:
    """A decoded constructor parameter."""
    name: str
    type: str
    value: Any
    raw_value: str  # Hex representation


@dataclass
class ConstructorInfo:
    """Information about contract constructor."""
    address: str
    creation_tx: str | None = None
    deployer: str | None = None
    block_number: int | None = None

    # Raw data
    creation_code: str = ""
    constructor_args_raw: str = ""

    # Decoded parameters
    parameters: list[ConstructorParam] = field(default_factory=list)

    # ABI info
    constructor_abi: dict | None = None
    decode_success: bool = False
    decode_error: str | None = None


# Common constructor parameter patterns
COMMON_PARAM_PATTERNS = {
    # Token-related
    "address": ["token", "tokenAddress", "_token", "underlying", "asset", "baseToken"],
    "uint256": ["fee", "feeRate", "feeBps", "initialSupply", "cap", "maxSupply"],
    "address": ["owner", "admin", "governance", "treasury", "feeRecipient"],
    "address": ["oracle", "priceOracle", "priceFeed", "chainlinkFeed"],
    "address": ["router", "factory", "pool", "vault"],
    "string": ["name", "symbol", "_name", "_symbol"],
    "uint8": ["decimals", "_decimals"],
    "bool": ["paused", "transferable", "mintable"],
}


class ConstructorExtractor(Tool):
    """Extract and decode constructor parameters from contract creation."""

    def __init__(self, chain_id: int, rpc_url: str | None = None):
        self.chain_id = chain_id
        self.rpc = RPCClient(chain_id, rpc_url)
        self.explorer = ExplorerClient(chain_id)

    @property
    def name(self) -> str:
        return "constructor_extractor"

    @property
    def description(self) -> str:
        return (
            "Extract constructor parameters from a contract's creation transaction. "
            "Decodes parameters using the contract ABI if available, or attempts "
            "heuristic decoding for common patterns."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "address": {
                    "type": "string",
                    "description": "Contract address to extract constructor params from",
                },
                "abi": {
                    "type": "array",
                    "description": "Optional ABI to use for decoding (if not provided, fetches from explorer)",
                },
            },
            "required": ["address"],
        }

    async def execute(
        self,
        address: str,
        abi: list[dict] | None = None,
    ) -> ToolResult:
        """Extract constructor parameters."""
        try:
            info = await self.extract(address, abi)
            summary = self._build_summary(info)

            return ToolResult(
                summary=summary,
                details={
                    "address": info.address,
                    "creation_tx": info.creation_tx,
                    "deployer": info.deployer,
                    "block_number": info.block_number,
                    "constructor_args_raw": info.constructor_args_raw,
                    "parameters": [
                        {
                            "name": p.name,
                            "type": p.type,
                            "value": self._serialize_value(p.value),
                            "raw_value": p.raw_value,
                        }
                        for p in info.parameters
                    ],
                    "decode_success": info.decode_success,
                    "decode_error": info.decode_error,
                },
                success=True,
            )

        except Exception as e:
            return ToolResult(
                summary=f"Constructor extraction failed: {str(e)}",
                success=False,
                error=str(e),
            )

    async def extract(
        self,
        address: str,
        abi: list[dict] | None = None,
    ) -> ConstructorInfo:
        """Extract constructor parameters from contract creation."""
        address = self.rpc.w3.to_checksum_address(address)
        info = ConstructorInfo(address=address)

        # Get creation transaction info
        creation_info = await self._get_creation_info(address)
        if creation_info:
            info.creation_tx = creation_info.get("txHash")
            info.deployer = creation_info.get("contractCreator")

        # Get creation transaction data
        if info.creation_tx:
            tx_data = await self._get_tx_data(info.creation_tx)
            if tx_data:
                info.creation_code = tx_data.get("input", "")
                info.block_number = tx_data.get("blockNumber")

        # Get deployed bytecode to separate constructor args
        if info.creation_code:
            deployed_code = await self.rpc.eth_get_code(address)
            info.constructor_args_raw = self._extract_constructor_args(
                info.creation_code, deployed_code
            )

        # Get ABI if not provided
        if abi is None:
            try:
                source_info = await self.explorer.get_contract_source(address)
                if source_info and source_info.get("ABI"):
                    import json
                    abi = json.loads(source_info["ABI"])
            except Exception:
                pass

        # Find constructor in ABI
        constructor_abi = None
        if abi:
            for item in abi:
                if item.get("type") == "constructor":
                    constructor_abi = item
                    info.constructor_abi = constructor_abi
                    break

        # Decode parameters
        if info.constructor_args_raw:
            if constructor_abi and constructor_abi.get("inputs"):
                info = self._decode_with_abi(info, constructor_abi)
            else:
                info = self._decode_heuristic(info)

        return info

    async def _get_creation_info(self, address: str) -> dict | None:
        """Get contract creation info from explorer."""
        try:
            # Etherscan API: contractcreation
            result = await self.explorer._request(
                "contract",
                "getcontractcreation",
                {"contractaddresses": address},
            )
            if result and isinstance(result, list) and len(result) > 0:
                return result[0]
        except Exception:
            pass
        return None

    async def _get_tx_data(self, tx_hash: str) -> dict | None:
        """Get transaction data."""
        try:
            tx = await self.rpc.eth_get_transaction_by_hash(tx_hash)
            if tx:
                return {
                    "input": tx.get("input", ""),
                    "blockNumber": (
                        int(tx["blockNumber"], 16)
                        if isinstance(tx.get("blockNumber"), str)
                        else tx.get("blockNumber")
                    ),
                    "from": tx.get("from"),
                }
        except Exception:
            pass
        return None

    def _extract_constructor_args(
        self,
        creation_code: str,
        deployed_code: str,
    ) -> str:
        """Extract constructor arguments from creation code."""
        if not creation_code or not deployed_code:
            return ""

        # Remove 0x prefix
        creation = creation_code[2:] if creation_code.startswith("0x") else creation_code
        deployed = deployed_code[2:] if deployed_code.startswith("0x") else deployed_code

        # The creation code = initcode + constructor args
        # After deployment, the deployed code is the runtime bytecode
        # Constructor args are appended after the initcode

        # Simple heuristic: find where deployed code ends in creation code
        # This isn't perfect but works for many cases

        # Method 1: Look for CODECOPY pattern end
        # The constructor args typically start after a recognizable pattern

        # Method 2: If creation code is longer than deployed code,
        # the difference might be constructor args
        if len(creation) > len(deployed):
            # Try to find where constructor args begin
            # Usually after the last STOP (00) or RETURN pattern

            # Look for common patterns that mark end of initcode
            # This is a simplified approach
            potential_args_start = len(creation) - 64  # Minimum 32 bytes of args

            # Walk backwards looking for valid arg boundary
            while potential_args_start > len(deployed):
                remaining = creation[potential_args_start:]
                # Constructor args should be 32-byte aligned
                if len(remaining) % 64 == 0:
                    return "0x" + remaining
                potential_args_start -= 64

        # Fallback: try to use Etherscan's constructor args directly
        return ""

    def _decode_with_abi(
        self,
        info: ConstructorInfo,
        constructor_abi: dict,
    ) -> ConstructorInfo:
        """Decode constructor args using ABI."""
        inputs = constructor_abi.get("inputs", [])
        if not inputs:
            info.decode_success = True
            return info

        # Build type list
        types = [inp["type"] for inp in inputs]
        names = [inp.get("name", f"param{i}") for i, inp in enumerate(inputs)]

        try:
            # Decode
            args_bytes = bytes.fromhex(
                info.constructor_args_raw[2:]
                if info.constructor_args_raw.startswith("0x")
                else info.constructor_args_raw
            )

            decoded = decode(types, args_bytes)

            for i, (name, type_, value) in enumerate(zip(names, types, decoded)):
                # Get raw hex for this parameter (approximate)
                raw_start = i * 32
                raw_end = raw_start + 32
                raw_hex = (
                    "0x" + info.constructor_args_raw[2:][raw_start * 2 : raw_end * 2]
                    if len(info.constructor_args_raw) > raw_start * 2 + 2
                    else ""
                )

                info.parameters.append(
                    ConstructorParam(
                        name=name,
                        type=type_,
                        value=value,
                        raw_value=raw_hex,
                    )
                )

            info.decode_success = True

        except DecodingError as e:
            info.decode_error = f"ABI decode failed: {str(e)}"
            info = self._decode_heuristic(info)

        except Exception as e:
            info.decode_error = f"Decode error: {str(e)}"

        return info

    def _decode_heuristic(self, info: ConstructorInfo) -> ConstructorInfo:
        """Attempt heuristic decoding of constructor args."""
        if not info.constructor_args_raw:
            return info

        args = (
            info.constructor_args_raw[2:]
            if info.constructor_args_raw.startswith("0x")
            else info.constructor_args_raw
        )

        # Process 32-byte chunks
        chunk_size = 64  # 32 bytes = 64 hex chars
        chunks = [args[i : i + chunk_size] for i in range(0, len(args), chunk_size)]

        for i, chunk in enumerate(chunks):
            if len(chunk) < chunk_size:
                chunk = chunk.ljust(chunk_size, "0")

            param = self._identify_chunk(chunk, i)
            info.parameters.append(param)

        info.decode_success = len(info.parameters) > 0

        return info

    def _identify_chunk(self, chunk: str, index: int) -> ConstructorParam:
        """Identify what type of data a 32-byte chunk contains."""
        raw_value = "0x" + chunk

        # Check for address (leading zeros + 20 bytes)
        if chunk[:24] == "0" * 24:
            potential_addr = "0x" + chunk[24:]
            if potential_addr != "0x" + "0" * 40:
                try:
                    addr = self.rpc.w3.to_checksum_address(potential_addr)
                    return ConstructorParam(
                        name=f"address_{index}",
                        type="address",
                        value=addr,
                        raw_value=raw_value,
                    )
                except Exception:
                    pass

        # Check for small numbers (likely uint8, uint16, etc.)
        try:
            value = int(chunk, 16)

            # Boolean
            if value == 0:
                return ConstructorParam(
                    name=f"value_{index}",
                    type="uint256",
                    value=0,
                    raw_value=raw_value,
                )
            elif value == 1:
                return ConstructorParam(
                    name=f"bool_{index}",
                    type="bool",
                    value=True,
                    raw_value=raw_value,
                )

            # Small numbers (likely fees, decimals, etc.)
            if value < 256:
                return ConstructorParam(
                    name=f"uint8_{index}",
                    type="uint8",
                    value=value,
                    raw_value=raw_value,
                )
            elif value < 10001:  # Likely basis points (0-10000)
                return ConstructorParam(
                    name=f"bps_{index}",
                    type="uint256",
                    value=value,
                    raw_value=raw_value,
                )
            else:
                return ConstructorParam(
                    name=f"uint256_{index}",
                    type="uint256",
                    value=value,
                    raw_value=raw_value,
                )

        except Exception:
            pass

        # Default: treat as bytes32
        return ConstructorParam(
            name=f"bytes32_{index}",
            type="bytes32",
            value=raw_value,
            raw_value=raw_value,
        )

    def _serialize_value(self, value: Any) -> Any:
        """Serialize value for JSON output."""
        if isinstance(value, bytes):
            return "0x" + value.hex()
        elif isinstance(value, int) and value > 2**53:
            return str(value)
        elif isinstance(value, (list, tuple)):
            return [self._serialize_value(v) for v in value]
        return value

    def _build_summary(self, info: ConstructorInfo) -> str:
        """Build human-readable summary."""
        lines = [
            "## Constructor Parameters",
            "",
            f"**Contract:** `{info.address}`",
        ]

        if info.creation_tx:
            lines.append(f"**Creation TX:** `{info.creation_tx}`")
        if info.deployer:
            lines.append(f"**Deployer:** `{info.deployer}`")
        if info.block_number:
            lines.append(f"**Block:** {info.block_number}")

        lines.append("")

        if info.parameters:
            lines.append("### Parameters")
            lines.append("")
            for p in info.parameters:
                value_str = str(p.value)
                if len(value_str) > 66:
                    value_str = value_str[:66] + "..."
                lines.append(f"- **{p.name}** ({p.type}): `{value_str}`")

            if not info.decode_success:
                lines.append("")
                lines.append("⚠️ Parameters decoded heuristically (no ABI available)")

        elif info.constructor_args_raw:
            lines.append("### Raw Constructor Arguments")
            lines.append("")
            lines.append(f"```")
            lines.append(info.constructor_args_raw[:200])
            if len(info.constructor_args_raw) > 200:
                lines.append("...")
            lines.append("```")

        else:
            lines.append("No constructor arguments found (possibly argless constructor)")

        if info.decode_error:
            lines.append("")
            lines.append(f"⚠️ Decode error: {info.decode_error}")

        return "\n".join(lines)

    async def close(self) -> None:
        """Close resources."""
        await self.rpc.close()
        await self.explorer.close()


async def extract_constructor(
    address: str,
    chain_id: int = 1,
    abi: list[dict] | None = None,
) -> ConstructorInfo:
    """Convenience function to extract constructor params."""
    extractor = ConstructorExtractor(chain_id)
    try:
        return await extractor.extract(address, abi)
    finally:
        await extractor.close()
