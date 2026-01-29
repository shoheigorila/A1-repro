"""Source Code Fetcher tool."""

from typing import Any

from a1.chain.explorer import ExplorerClient
from a1.tools.base import Tool, ToolResult


class SourceCodeFetcher(Tool):
    """Fetch verified source code from block explorers."""

    def __init__(self, chain_id: int):
        self.chain_id = chain_id
        self.explorer = ExplorerClient(chain_id)

    @property
    def name(self) -> str:
        return "source_code_fetcher"

    @property
    def description(self) -> str:
        return (
            "Fetch verified source code for a smart contract from the block explorer. "
            "Returns contract name, compiler version, source files, and ABI."
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
                }
            },
            "required": ["address"],
        }

    async def execute(self, address: str) -> ToolResult:
        """Fetch source code for a contract."""
        try:
            source = await self.explorer.get_contract_source(address)

            if not source.get("verified"):
                return ToolResult(
                    summary=f"Contract {address} is not verified.",
                    details={"verified": False, "address": address},
                    success=False,
                    error="Contract not verified",
                )

            # Build summary
            source_files = source.get("source_files", {})
            file_list = list(source_files.keys())

            summary_lines = [
                f"Contract: {source['contract_name']}",
                f"Compiler: {source['compiler_version']}",
                f"Optimization: {'Yes' if source['optimization_used'] else 'No'} ({source['runs']} runs)",
                f"Files: {len(file_list)}",
            ]

            if source.get("proxy"):
                summary_lines.append(f"Proxy: Yes (impl: {source.get('implementation', 'unknown')})")

            # Include main source code in summary (truncated)
            main_file = None
            for path, content in source_files.items():
                if source["contract_name"] in path or path == "main.sol":
                    main_file = (path, content)
                    break
            if not main_file and source_files:
                main_file = next(iter(source_files.items()))

            if main_file:
                path, content = main_file
                # Truncate if too long
                if len(content) > 5000:
                    content = content[:5000] + "\n... (truncated)"
                summary_lines.append(f"\n--- {path} ---\n{content}")

            return ToolResult(
                summary="\n".join(summary_lines),
                details={
                    "verified": True,
                    "address": address,
                    "contract_name": source["contract_name"],
                    "compiler_version": source["compiler_version"],
                    "optimization_used": source["optimization_used"],
                    "runs": source["runs"],
                    "source_files": source_files,
                    "abi": source["abi"],
                    "proxy": source.get("proxy", False),
                    "implementation": source.get("implementation"),
                    "constructor_arguments": source.get("constructor_arguments"),
                },
                artifacts=file_list,
                cache_key=f"source:{self.chain_id}:{address.lower()}",
            )

        except Exception as e:
            return ToolResult(
                summary=f"Failed to fetch source code: {str(e)}",
                success=False,
                error=str(e),
            )

    async def close(self) -> None:
        """Close the explorer client."""
        await self.explorer.close()
