"""Parse Solidity code from LLM responses."""

import re
from dataclasses import dataclass


@dataclass
class ParsedStrategy:
    """Parsed strategy information."""
    code: str
    contract_name: str
    has_run_function: bool
    imports: list[str]
    interfaces: list[str]


class StrategyParser:
    """Parse Strategy code from LLM responses."""

    # Regex patterns
    SOLIDITY_BLOCK_PATTERN = re.compile(
        r"```(?:solidity|sol)?\s*\n(.*?)```",
        re.DOTALL | re.IGNORECASE,
    )

    CONTRACT_PATTERN = re.compile(
        r"contract\s+(\w+)\s+(?:is\s+[\w\s,]+\s*)?\{",
        re.DOTALL,
    )

    FUNCTION_PATTERN = re.compile(
        r"function\s+run\s*\(\s*\)\s*(?:external|public)",
    )

    IMPORT_PATTERN = re.compile(
        r'^import\s+.*?;',
        re.MULTILINE,
    )

    INTERFACE_PATTERN = re.compile(
        r"interface\s+(\w+)\s*\{",
    )

    def parse(self, response: str) -> ParsedStrategy | None:
        """Parse Strategy code from response."""
        # Extract solidity code blocks
        matches = self.SOLIDITY_BLOCK_PATTERN.findall(response)

        if not matches:
            return None

        # Use the last/longest code block (often the final refined version)
        code = max(matches, key=len)
        code = code.strip()

        # Extract contract name
        contract_matches = self.CONTRACT_PATTERN.findall(code)
        contract_name = "Strategy"
        for name in contract_matches:
            if name.lower() == "strategy":
                contract_name = name
                break
        if contract_matches and contract_name == "Strategy":
            # If no Strategy found, use the last contract
            contract_name = contract_matches[-1]

        # Check for run function
        has_run = bool(self.FUNCTION_PATTERN.search(code))

        # Extract imports
        imports = self.IMPORT_PATTERN.findall(code)

        # Extract interfaces
        interfaces = self.INTERFACE_PATTERN.findall(code)

        return ParsedStrategy(
            code=code,
            contract_name=contract_name,
            has_run_function=has_run,
            imports=imports,
            interfaces=interfaces,
        )

    def validate(self, parsed: ParsedStrategy) -> list[str]:
        """Validate parsed strategy. Returns list of issues."""
        issues = []

        if not parsed.has_run_function:
            issues.append("Missing run() function")

        if "Strategy" not in parsed.contract_name:
            issues.append(f"Contract should be named 'Strategy', found '{parsed.contract_name}'")

        # Check for pragma
        if "pragma solidity" not in parsed.code:
            issues.append("Missing pragma statement")

        # Check for basic structure
        if "contract" not in parsed.code:
            issues.append("No contract definition found")

        return issues

    def fix_common_issues(self, code: str) -> str:
        """Attempt to fix common issues in generated code."""
        lines = code.splitlines()
        result = []

        # Ensure pragma at top
        has_pragma = any("pragma solidity" in line for line in lines)
        if not has_pragma:
            result.append("// SPDX-License-Identifier: MIT")
            result.append("pragma solidity ^0.8.20;")
            result.append("")

        for line in lines:
            result.append(line)

        code = "\n".join(result)

        # Ensure IStrategy interface exists
        if "interface IStrategy" not in code and "IStrategy" in code:
            interface_code = """
interface IStrategy {
    function run() external;
}
"""
            # Insert after pragma
            pragma_end = code.find(";") + 1
            code = code[:pragma_end] + "\n" + interface_code + code[pragma_end:]

        # Ensure receive function exists
        if "receive()" not in code and "external payable" not in code:
            # Find last closing brace of Strategy contract
            last_brace = code.rfind("}")
            if last_brace > 0:
                receive_code = "\n    receive() external payable {}\n"
                code = code[:last_brace] + receive_code + code[last_brace:]

        return code

    def extract_all_code_blocks(self, response: str) -> list[str]:
        """Extract all code blocks from response."""
        return self.SOLIDITY_BLOCK_PATTERN.findall(response)

    def merge_code_blocks(self, blocks: list[str]) -> str:
        """Merge multiple code blocks, deduplicating contracts."""
        seen_contracts: set[str] = set()
        seen_interfaces: set[str] = set()
        merged_parts: list[str] = []

        # Add pragma once
        merged_parts.append("// SPDX-License-Identifier: MIT")
        merged_parts.append("pragma solidity ^0.8.20;")
        merged_parts.append("")

        for block in blocks:
            # Remove pragma and license from this block
            block = re.sub(r"//\s*SPDX-License-Identifier:.*\n?", "", block)
            block = re.sub(r"pragma\s+solidity[^;]+;", "", block)
            block = re.sub(r'^import\s+.*?;', "", block, flags=re.MULTILINE)

            # Extract and add new interfaces
            for match in re.finditer(r"(interface\s+(\w+)\s*\{[^}]*\})", block, re.DOTALL):
                interface_code, interface_name = match.groups()
                if interface_name not in seen_interfaces:
                    seen_interfaces.add(interface_name)
                    merged_parts.append(interface_code.strip())
                    merged_parts.append("")

            # Extract and add new contracts
            for match in re.finditer(
                r"((?:abstract\s+)?contract\s+(\w+)[^{]*\{(?:[^{}]|\{[^{}]*\})*\})",
                block,
                re.DOTALL,
            ):
                contract_code, contract_name = match.groups()
                if contract_name not in seen_contracts:
                    seen_contracts.add(contract_name)
                    merged_parts.append(contract_code.strip())
                    merged_parts.append("")

        return "\n".join(merged_parts)
