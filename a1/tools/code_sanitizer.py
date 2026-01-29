"""Code Sanitizer tool."""

import re
from typing import Any

from a1.tools.base import Tool, ToolResult


class CodeSanitizer(Tool):
    """Sanitize and clean Solidity code."""

    @property
    def name(self) -> str:
        return "code_sanitizer"

    @property
    def description(self) -> str:
        return (
            "Clean and sanitize Solidity code by removing comments, "
            "normalizing whitespace, and organizing imports/pragmas."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Solidity source code to sanitize",
                },
                "remove_comments": {
                    "type": "boolean",
                    "description": "Remove all comments",
                    "default": True,
                },
                "remove_imports": {
                    "type": "boolean",
                    "description": "Remove import statements",
                    "default": False,
                },
                "normalize_whitespace": {
                    "type": "boolean",
                    "description": "Normalize whitespace and blank lines",
                    "default": True,
                },
            },
            "required": ["code"],
        }

    async def execute(
        self,
        code: str,
        remove_comments: bool = True,
        remove_imports: bool = False,
        normalize_whitespace: bool = True,
    ) -> ToolResult:
        """Sanitize Solidity code."""
        try:
            original_lines = len(code.splitlines())
            sanitized = code

            if remove_comments:
                sanitized = self._remove_comments(sanitized)

            if remove_imports:
                sanitized = self._remove_imports(sanitized)

            if normalize_whitespace:
                sanitized = self._normalize_whitespace(sanitized)

            final_lines = len(sanitized.splitlines())

            return ToolResult(
                summary=f"Sanitized code: {original_lines} -> {final_lines} lines",
                details={
                    "original_lines": original_lines,
                    "final_lines": final_lines,
                    "sanitized_code": sanitized,
                },
                artifacts=[],
            )

        except Exception as e:
            return ToolResult(
                summary=f"Failed to sanitize code: {str(e)}",
                success=False,
                error=str(e),
            )

    def _remove_comments(self, code: str) -> str:
        """Remove all comments from Solidity code."""
        # Remove single-line comments (// ...)
        code = re.sub(r"//.*$", "", code, flags=re.MULTILINE)

        # Remove multi-line comments (/* ... */)
        code = re.sub(r"/\*[\s\S]*?\*/", "", code)

        # Remove NatSpec comments (/// ...)
        code = re.sub(r"///.*$", "", code, flags=re.MULTILINE)

        return code

    def _remove_imports(self, code: str) -> str:
        """Remove import statements."""
        # Remove import statements
        code = re.sub(r'^import\s+.*?;', "", code, flags=re.MULTILINE)
        return code

    def _normalize_whitespace(self, code: str) -> str:
        """Normalize whitespace and blank lines."""
        lines = code.splitlines()

        # Remove trailing whitespace
        lines = [line.rstrip() for line in lines]

        # Remove excessive blank lines (keep max 1)
        result = []
        prev_blank = False
        for line in lines:
            is_blank = not line.strip()
            if is_blank:
                if not prev_blank:
                    result.append(line)
                prev_blank = True
            else:
                result.append(line)
                prev_blank = False

        # Remove leading/trailing blank lines
        while result and not result[0].strip():
            result.pop(0)
        while result and not result[-1].strip():
            result.pop()

        return "\n".join(result)

    def extract_contract_names(self, code: str) -> list[str]:
        """Extract contract/interface/library names from code."""
        pattern = r"(?:contract|interface|library|abstract contract)\s+(\w+)"
        return re.findall(pattern, code)

    def extract_imports(self, code: str) -> list[str]:
        """Extract import paths from code."""
        pattern = r'import\s+(?:.*?\s+from\s+)?["\']([^"\']+)["\']'
        return re.findall(pattern, code)

    def extract_pragma(self, code: str) -> str | None:
        """Extract pragma statement."""
        match = re.search(r"pragma\s+solidity\s+([^;]+);", code)
        return match.group(1).strip() if match else None

    def merge_sources(
        self,
        sources: dict[str, str],
        main_contract: str | None = None,
    ) -> str:
        """Merge multiple source files into single file."""
        # Collect all pragmas
        pragmas: set[str] = set()
        for code in sources.values():
            pragma = self.extract_pragma(code)
            if pragma:
                pragmas.add(pragma)

        # Use most permissive pragma or first one
        pragma_line = ""
        if pragmas:
            # Sort by version, take latest
            sorted_pragmas = sorted(pragmas, reverse=True)
            pragma_line = f"pragma solidity {sorted_pragmas[0]};\n\n"

        # Collect and dedupe contracts
        seen_contracts: set[str] = set()
        merged_parts: list[str] = [pragma_line]

        # Process main contract last if specified
        file_order = list(sources.keys())
        if main_contract:
            for path in file_order[:]:
                if main_contract in path:
                    file_order.remove(path)
                    file_order.append(path)
                    break

        for path in file_order:
            code = sources[path]

            # Remove pragma and imports
            code = re.sub(r"pragma\s+solidity\s+[^;]+;", "", code)
            code = re.sub(r'^import\s+.*?;', "", code, flags=re.MULTILINE)

            # Extract contracts
            contracts = self.extract_contract_names(code)
            new_contracts = [c for c in contracts if c not in seen_contracts]

            if new_contracts:
                seen_contracts.update(new_contracts)
                code = self._normalize_whitespace(code)
                if code.strip():
                    merged_parts.append(f"// From: {path}\n{code}")

        return "\n\n".join(merged_parts)
