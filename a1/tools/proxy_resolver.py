"""Proxy Resolver - Detect and resolve proxy contracts."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from a1.chain.rpc import RPCClient
from a1.chain.explorer import ExplorerClient
from a1.tools.base import Tool, ToolResult


class ProxyType(Enum):
    """Types of proxy patterns."""
    NONE = "none"
    EIP1967_TRANSPARENT = "eip1967_transparent"
    EIP1967_BEACON = "eip1967_beacon"
    EIP1167_MINIMAL = "eip1167_minimal"
    UUPS = "uups"
    CUSTOM_SLOT = "custom_slot"
    UNKNOWN = "unknown"


@dataclass
class ProxyInfo:
    """Information about a proxy contract."""
    address: str
    proxy_type: ProxyType
    implementation_address: str | None = None
    beacon_address: str | None = None
    admin_address: str | None = None

    # For nested proxies
    nested_implementations: list[str] = field(default_factory=list)

    # Detection metadata
    detection_method: str = ""
    confidence: float = 1.0


# EIP-1967 storage slots
EIP1967_IMPL_SLOT = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
EIP1967_BEACON_SLOT = "0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50"
EIP1967_ADMIN_SLOT = "0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103"

# EIP-1167 minimal proxy bytecode patterns
EIP1167_PREFIX = "363d3d373d3d3d363d73"
EIP1167_SUFFIX = "5af43d82803e903d91602b57fd5bf3"

# Common custom storage slots (used by some protocols)
CUSTOM_IMPL_SLOTS = [
    "0x7050c9e0f4ca769c69bd3a8ef740bc37934f8e2c036e5a723fd8ee048ed3f8c3",  # OpenZeppelin old
    "0xc5f16f0fcc639fa48a6947836d9850f504798523bf8c9a3a87d5876cf622bcf7",  # Compound
]


class ProxyResolver(Tool):
    """Detect and resolve proxy contracts to their implementations."""

    def __init__(self, chain_id: int, rpc_url: str | None = None):
        self.chain_id = chain_id
        self.rpc = RPCClient(chain_id, rpc_url)
        self.explorer = ExplorerClient(chain_id)

    @property
    def name(self) -> str:
        return "proxy_resolver"

    @property
    def description(self) -> str:
        return (
            "Detect if a contract is a proxy and resolve to its implementation. "
            "Supports EIP-1967 (transparent, beacon), EIP-1167 (minimal/clone), "
            "UUPS, and common custom proxy patterns."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "address": {
                    "type": "string",
                    "description": "Contract address to check for proxy pattern",
                },
                "resolve_nested": {
                    "type": "boolean",
                    "description": "Recursively resolve nested proxies",
                    "default": True,
                },
            },
            "required": ["address"],
        }

    async def execute(
        self,
        address: str,
        resolve_nested: bool = True,
    ) -> ToolResult:
        """Detect and resolve proxy contract."""
        try:
            info = await self.resolve(address, resolve_nested)
            summary = self._build_summary(info)

            return ToolResult(
                summary=summary,
                details={
                    "address": info.address,
                    "proxy_type": info.proxy_type.value,
                    "implementation_address": info.implementation_address,
                    "beacon_address": info.beacon_address,
                    "admin_address": info.admin_address,
                    "nested_implementations": info.nested_implementations,
                    "detection_method": info.detection_method,
                    "confidence": info.confidence,
                },
                success=True,
            )

        except Exception as e:
            return ToolResult(
                summary=f"Proxy resolution failed: {str(e)}",
                success=False,
                error=str(e),
            )

    async def resolve(
        self,
        address: str,
        resolve_nested: bool = True,
        max_depth: int = 5,
    ) -> ProxyInfo:
        """Resolve proxy to implementation address."""
        address = self.rpc.w3.to_checksum_address(address)

        # Try different detection methods in order
        info = await self._try_eip1967(address)
        if info.proxy_type == ProxyType.NONE:
            info = await self._try_eip1167(address)
        if info.proxy_type == ProxyType.NONE:
            info = await self._try_custom_slots(address)
        if info.proxy_type == ProxyType.NONE:
            info = await self._try_implementation_function(address)

        # Resolve nested proxies
        if resolve_nested and info.implementation_address and max_depth > 0:
            nested_info = await self.resolve(
                info.implementation_address,
                resolve_nested=True,
                max_depth=max_depth - 1,
            )
            if nested_info.proxy_type != ProxyType.NONE:
                info.nested_implementations.append(info.implementation_address)
                if nested_info.implementation_address:
                    info.nested_implementations.extend(nested_info.nested_implementations)
                    info.implementation_address = nested_info.implementation_address

        return info

    async def _try_eip1967(self, address: str) -> ProxyInfo:
        """Try EIP-1967 proxy detection."""
        info = ProxyInfo(address=address, proxy_type=ProxyType.NONE)

        # Check implementation slot
        impl_addr = await self._read_storage_slot(address, EIP1967_IMPL_SLOT)
        if impl_addr and impl_addr != "0x" + "0" * 40:
            info.proxy_type = ProxyType.EIP1967_TRANSPARENT
            info.implementation_address = impl_addr
            info.detection_method = "EIP-1967 implementation slot"

            # Try to get admin
            admin_addr = await self._read_storage_slot(address, EIP1967_ADMIN_SLOT)
            if admin_addr and admin_addr != "0x" + "0" * 40:
                info.admin_address = admin_addr

            return info

        # Check beacon slot
        beacon_addr = await self._read_storage_slot(address, EIP1967_BEACON_SLOT)
        if beacon_addr and beacon_addr != "0x" + "0" * 40:
            info.proxy_type = ProxyType.EIP1967_BEACON
            info.beacon_address = beacon_addr
            info.detection_method = "EIP-1967 beacon slot"

            # Get implementation from beacon
            impl_addr = await self._get_beacon_implementation(beacon_addr)
            if impl_addr:
                info.implementation_address = impl_addr

            return info

        return info

    async def _try_eip1167(self, address: str) -> ProxyInfo:
        """Try EIP-1167 minimal proxy detection."""
        info = ProxyInfo(address=address, proxy_type=ProxyType.NONE)

        try:
            code = await self.rpc.eth_get_code(address)
            if not code or code == "0x":
                return info

            # Remove 0x prefix
            code = code[2:].lower()

            # Check for EIP-1167 pattern
            # Pattern: 363d3d373d3d3d363d73<address>5af43d82803e903d91602b57fd5bf3
            if code.startswith(EIP1167_PREFIX.lower()):
                # Extract implementation address (20 bytes = 40 hex chars)
                addr_start = len(EIP1167_PREFIX)
                addr_end = addr_start + 40

                if len(code) >= addr_end:
                    impl_addr = "0x" + code[addr_start:addr_end]

                    # Validate it's a valid address
                    if len(impl_addr) == 42:
                        info.proxy_type = ProxyType.EIP1167_MINIMAL
                        info.implementation_address = self.rpc.w3.to_checksum_address(impl_addr)
                        info.detection_method = "EIP-1167 bytecode pattern"

        except Exception:
            pass

        return info

    async def _try_custom_slots(self, address: str) -> ProxyInfo:
        """Try common custom storage slots."""
        info = ProxyInfo(address=address, proxy_type=ProxyType.NONE)

        for slot in CUSTOM_IMPL_SLOTS:
            impl_addr = await self._read_storage_slot(address, slot)
            if impl_addr and impl_addr != "0x" + "0" * 40:
                info.proxy_type = ProxyType.CUSTOM_SLOT
                info.implementation_address = impl_addr
                info.detection_method = f"Custom storage slot: {slot[:18]}..."
                info.confidence = 0.8  # Lower confidence for custom slots
                return info

        return info

    async def _try_implementation_function(self, address: str) -> ProxyInfo:
        """Try calling implementation() or similar functions."""
        info = ProxyInfo(address=address, proxy_type=ProxyType.NONE)

        # Common implementation getter function signatures
        impl_selectors = [
            ("implementation()", "5c60da1b"),
            ("getImplementation()", "aaf10f42"),
            ("masterCopy()", "a619486e"),  # Gnosis Safe
            ("childImplementation()", "1e52b518"),
        ]

        for func_name, selector in impl_selectors:
            try:
                result = await self.rpc.eth_call(address, "0x" + selector)
                if result and result != "0x" and len(result) >= 66:
                    # Decode address from result (last 40 chars of 32-byte word)
                    impl_addr = "0x" + result[-40:]

                    if impl_addr != "0x" + "0" * 40:
                        # Verify it's a contract
                        code = await self.rpc.eth_get_code(impl_addr)
                        if code and code != "0x":
                            info.proxy_type = ProxyType.UUPS
                            info.implementation_address = self.rpc.w3.to_checksum_address(impl_addr)
                            info.detection_method = f"Function call: {func_name}"
                            return info

            except Exception:
                continue

        return info

    async def _read_storage_slot(self, address: str, slot: str) -> str | None:
        """Read a storage slot and extract address."""
        try:
            result = await self.rpc.eth_get_storage_at(address, slot)
            if result and result != "0x" and len(result) >= 66:
                # Extract address (last 40 hex chars)
                addr = "0x" + result[-40:]
                return self.rpc.w3.to_checksum_address(addr)
        except Exception:
            pass
        return None

    async def _get_beacon_implementation(self, beacon_address: str) -> str | None:
        """Get implementation address from a beacon contract."""
        try:
            # Call implementation() on beacon
            result = await self.rpc.eth_call(beacon_address, "0x5c60da1b")
            if result and result != "0x" and len(result) >= 66:
                impl_addr = "0x" + result[-40:]
                if impl_addr != "0x" + "0" * 40:
                    return self.rpc.w3.to_checksum_address(impl_addr)
        except Exception:
            pass
        return None

    def _build_summary(self, info: ProxyInfo) -> str:
        """Build human-readable summary."""
        if info.proxy_type == ProxyType.NONE:
            return f"## Proxy Detection\n\nAddress `{info.address}` is **not a proxy** (or uses an unknown pattern)."

        lines = [
            "## Proxy Detection",
            "",
            f"**Address:** `{info.address}`",
            f"**Type:** {info.proxy_type.value}",
            f"**Detection:** {info.detection_method}",
            "",
        ]

        if info.implementation_address:
            lines.append(f"**Implementation:** `{info.implementation_address}`")

        if info.beacon_address:
            lines.append(f"**Beacon:** `{info.beacon_address}`")

        if info.admin_address:
            lines.append(f"**Admin:** `{info.admin_address}`")

        if info.nested_implementations:
            lines.append("")
            lines.append("**Proxy Chain:**")
            for i, addr in enumerate(info.nested_implementations):
                lines.append(f"  {i+1}. `{addr}`")
            lines.append(f"  → Final: `{info.implementation_address}`")

        if info.confidence < 1.0:
            lines.append("")
            lines.append(f"⚠️ Confidence: {info.confidence:.0%}")

        return "\n".join(lines)

    async def close(self) -> None:
        """Close resources."""
        await self.rpc.close()
        await self.explorer.close()


async def resolve_proxy(
    address: str,
    chain_id: int = 1,
) -> ProxyInfo:
    """Convenience function to resolve a proxy."""
    resolver = ProxyResolver(chain_id)
    try:
        return await resolver.resolve(address)
    finally:
        await resolver.close()
