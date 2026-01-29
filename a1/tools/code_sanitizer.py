"""Code Sanitizer tool with AST analysis capabilities."""

import re
from dataclasses import dataclass, field
from typing import Any

from a1.tools.base import Tool, ToolResult


@dataclass
class ContractDefinition:
    """Represents a contract/interface/library definition."""
    name: str
    type: str  # "contract", "interface", "library", "abstract contract"
    inherits: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    state_variables: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    modifiers: list[str] = field(default_factory=list)
    start_line: int = 0
    end_line: int = 0


@dataclass
class DependencyGraph:
    """Graph of contract dependencies."""
    contracts: dict[str, ContractDefinition] = field(default_factory=dict)
    imports: dict[str, list[str]] = field(default_factory=dict)  # file -> imported files
    inheritance: dict[str, list[str]] = field(default_factory=dict)  # contract -> parents
    usages: dict[str, set[str]] = field(default_factory=dict)  # contract -> used contracts


class ASTAnalyzer:
    """Analyze Solidity code structure (regex-based pseudo-AST)."""

    def __init__(self):
        self.contracts: dict[str, ContractDefinition] = {}
        self.imports: list[tuple[str, str]] = []  # (import_path, alias)
        self.pragma: str | None = None

    def analyze(self, code: str) -> DependencyGraph:
        """Analyze Solidity code and build dependency graph."""
        graph = DependencyGraph()

        # Extract pragma
        self.pragma = self._extract_pragma(code)

        # Extract imports
        self.imports = self._extract_imports_detailed(code)
        graph.imports["main"] = [imp[0] for imp in self.imports]

        # Extract contract definitions
        self.contracts = self._extract_contracts(code)
        graph.contracts = self.contracts

        # Build inheritance graph
        for name, contract in self.contracts.items():
            graph.inheritance[name] = contract.inherits

        # Analyze usages
        graph.usages = self._analyze_usages(code)

        return graph

    def _extract_pragma(self, code: str) -> str | None:
        """Extract pragma version."""
        match = re.search(r"pragma\s+solidity\s+([^;]+);", code)
        return match.group(1).strip() if match else None

    def _extract_imports_detailed(self, code: str) -> list[tuple[str, str]]:
        """Extract imports with aliases."""
        imports = []

        # Pattern: import "path";
        for match in re.finditer(r'import\s+["\']([^"\']+)["\'];', code):
            imports.append((match.group(1), ""))

        # Pattern: import "path" as alias;
        for match in re.finditer(r'import\s+["\']([^"\']+)["\']\s+as\s+(\w+);', code):
            imports.append((match.group(1), match.group(2)))

        # Pattern: import {A, B} from "path";
        for match in re.finditer(r'import\s+\{([^}]+)\}\s+from\s+["\']([^"\']+)["\'];', code):
            names = [n.strip() for n in match.group(1).split(",")]
            path = match.group(2)
            for name in names:
                # Handle "Name as Alias"
                if " as " in name:
                    parts = name.split(" as ")
                    imports.append((path + "#" + parts[0].strip(), parts[1].strip()))
                else:
                    imports.append((path + "#" + name, name))

        return imports

    def _extract_contracts(self, code: str) -> dict[str, ContractDefinition]:
        """Extract contract definitions with details."""
        contracts = {}

        # Pattern for contract/interface/library declaration
        pattern = r"(abstract\s+contract|contract|interface|library)\s+(\w+)(?:\s+is\s+([^{]+))?\s*\{"

        lines = code.splitlines()
        for i, line in enumerate(lines):
            match = re.search(pattern, line)
            if match:
                contract_type = match.group(1)
                name = match.group(2)
                inherits_str = match.group(3) or ""

                # Parse inheritance list
                inherits = []
                if inherits_str:
                    # Split by comma, handle constructor args like Base(arg)
                    parts = re.split(r",\s*(?![^(]*\))", inherits_str)
                    for part in parts:
                        parent = re.match(r"(\w+)", part.strip())
                        if parent:
                            inherits.append(parent.group(1))

                # Find contract end (count braces)
                end_line = self._find_block_end(lines, i)

                # Extract functions, events, modifiers, state vars from block
                block = "\n".join(lines[i:end_line + 1])
                functions = self._extract_functions(block)
                events = self._extract_events(block)
                modifiers = self._extract_modifiers(block)
                state_vars = self._extract_state_variables(block)

                contracts[name] = ContractDefinition(
                    name=name,
                    type=contract_type,
                    inherits=inherits,
                    functions=functions,
                    events=events,
                    modifiers=modifiers,
                    state_variables=state_vars,
                    start_line=i + 1,
                    end_line=end_line + 1,
                )

        return contracts

    def _find_block_end(self, lines: list[str], start: int) -> int:
        """Find the closing brace of a block."""
        depth = 0
        in_string = False
        string_char = None

        for i in range(start, len(lines)):
            line = lines[i]
            j = 0
            while j < len(line):
                char = line[j]

                # Handle string literals
                if char in "\"'" and (j == 0 or line[j-1] != "\\"):
                    if not in_string:
                        in_string = True
                        string_char = char
                    elif char == string_char:
                        in_string = False
                        string_char = None

                if not in_string:
                    if char == "{":
                        depth += 1
                    elif char == "}":
                        depth -= 1
                        if depth == 0:
                            return i

                j += 1

        return len(lines) - 1

    def _extract_functions(self, block: str) -> list[str]:
        """Extract function names from a contract block."""
        pattern = r"function\s+(\w+)\s*\("
        return re.findall(pattern, block)

    def _extract_events(self, block: str) -> list[str]:
        """Extract event names from a contract block."""
        pattern = r"event\s+(\w+)\s*\("
        return re.findall(pattern, block)

    def _extract_modifiers(self, block: str) -> list[str]:
        """Extract modifier names from a contract block."""
        pattern = r"modifier\s+(\w+)\s*[(\{]"
        return re.findall(pattern, block)

    def _extract_state_variables(self, block: str) -> list[str]:
        """Extract state variable names (simplified)."""
        # This is a simplified extraction
        pattern = r"(?:uint\d*|int\d*|address|bool|bytes\d*|string|mapping)[^;]*\s+(\w+)\s*[;=]"
        return re.findall(pattern, block)

    def _analyze_usages(self, code: str) -> dict[str, set[str]]:
        """Analyze which contracts use which other contracts."""
        usages: dict[str, set[str]] = {}

        for name, contract in self.contracts.items():
            usages[name] = set(contract.inherits)

            # Extract block for this contract
            lines = code.splitlines()
            block = "\n".join(lines[contract.start_line - 1:contract.end_line])

            # Look for type references
            for other_name in self.contracts:
                if other_name != name:
                    # Check for type usage patterns
                    patterns = [
                        rf"\b{other_name}\s*\(",  # Constructor call
                        rf"\b{other_name}\s+\w+",  # Variable declaration
                        rf"\({other_name}\)",  # Type cast
                        rf"\b{other_name}\.",  # Static call
                    ]
                    for pattern in patterns:
                        if re.search(pattern, block):
                            usages[name].add(other_name)
                            break

        return usages

    def get_required_contracts(
        self,
        target: str,
        graph: DependencyGraph,
    ) -> set[str]:
        """Get all contracts required by target (transitive closure)."""
        required = set()
        to_process = [target]

        while to_process:
            current = to_process.pop()
            if current in required:
                continue
            required.add(current)

            # Add inherited contracts
            if current in graph.inheritance:
                to_process.extend(graph.inheritance[current])

            # Add used contracts
            if current in graph.usages:
                to_process.extend(graph.usages[current])

        return required

    def extract_minimal_source(
        self,
        code: str,
        target_contract: str,
    ) -> str:
        """Extract minimal source containing only required definitions."""
        graph = self.analyze(code)

        if target_contract not in graph.contracts:
            return code  # Return original if target not found

        required = self.get_required_contracts(target_contract, graph)

        # Build minimal source
        lines = code.splitlines()
        parts = []

        # Add pragma
        if self.pragma:
            parts.append(f"pragma solidity {self.pragma};")
            parts.append("")

        # Add required contracts in dependency order
        ordered = self._topological_sort(required, graph)

        for name in ordered:
            if name in graph.contracts:
                contract = graph.contracts[name]
                contract_code = "\n".join(lines[contract.start_line - 1:contract.end_line])
                parts.append(contract_code)
                parts.append("")

        return "\n".join(parts)

    def _topological_sort(
        self,
        contracts: set[str],
        graph: DependencyGraph,
    ) -> list[str]:
        """Sort contracts in dependency order (dependencies first)."""
        result = []
        visited = set()
        temp_mark = set()

        def visit(name: str):
            if name in temp_mark:
                return  # Cycle detected, skip
            if name in visited:
                return

            temp_mark.add(name)

            # Visit dependencies first
            if name in graph.inheritance:
                for dep in graph.inheritance[name]:
                    if dep in contracts:
                        visit(dep)
            if name in graph.usages:
                for dep in graph.usages[name]:
                    if dep in contracts:
                        visit(dep)

            temp_mark.remove(name)
            visited.add(name)
            result.append(name)

        for name in contracts:
            visit(name)

        return result


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

    def analyze_dependencies(self, code: str) -> DependencyGraph:
        """Analyze code structure and dependencies using AST analysis."""
        analyzer = ASTAnalyzer()
        return analyzer.analyze(code)

    def extract_minimal(
        self,
        code: str,
        target_contract: str,
    ) -> str:
        """Extract minimal source containing only required definitions."""
        analyzer = ASTAnalyzer()
        return analyzer.extract_minimal_source(code, target_contract)

    def find_unused_contracts(self, code: str, entry_points: list[str]) -> list[str]:
        """Find contracts not reachable from entry points."""
        analyzer = ASTAnalyzer()
        graph = analyzer.analyze(code)

        # Get all required contracts from entry points
        required: set[str] = set()
        for entry in entry_points:
            if entry in graph.contracts:
                required.update(analyzer.get_required_contracts(entry, graph))

        # Find unused
        all_contracts = set(graph.contracts.keys())
        unused = all_contracts - required

        return list(unused)

    def get_contract_info(self, code: str) -> dict[str, Any]:
        """Get detailed information about contracts in code."""
        analyzer = ASTAnalyzer()
        graph = analyzer.analyze(code)

        info = {
            "pragma": analyzer.pragma,
            "imports": [imp[0] for imp in analyzer.imports],
            "contracts": {},
        }

        for name, contract in graph.contracts.items():
            info["contracts"][name] = {
                "type": contract.type,
                "inherits": contract.inherits,
                "functions": contract.functions,
                "events": contract.events,
                "modifiers": contract.modifiers,
                "state_variables": contract.state_variables,
                "lines": (contract.start_line, contract.end_line),
            }

        return info
