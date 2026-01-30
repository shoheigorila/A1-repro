"""Command-line interface for A1."""

import asyncio
import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

app = typer.Typer(help="A1: Autonomous PoC Generator")
console = Console()


@app.command()
def run(
    target: str = typer.Argument(..., help="Target contract address"),
    chain_id: int = typer.Option(1, "--chain", "-c", help="Chain ID (1=Ethereum, 56=BSC)"),
    block: Optional[int] = typer.Option(None, "--block", "-b", help="Block number to fork from"),
    model: str = typer.Option("gpt-4-turbo", "--model", "-m", help="LLM model to use"),
    provider: str = typer.Option("openai", "--provider", "-p", help="LLM provider (openai/anthropic/openrouter)"),
    max_turns: int = typer.Option(5, "--max-turns", "-t", help="Maximum turns"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file for results"),
    rpc_url: Optional[str] = typer.Option(None, "--rpc", help="Custom RPC URL"),
):
    """Run the agent on a target contract."""
    console.print(f"[bold blue]A1 Autonomous PoC Generator[/bold blue]")
    console.print(f"Target: {target}")
    console.print(f"Chain: {chain_id}")
    console.print(f"Model: {model} ({provider})")
    console.print()

    async def _run():
        from a1.controller.loop import run_agent

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Running agent...", total=None)

            result = await run_agent(
                target_address=target,
                chain_id=chain_id,
                block_number=block,
                model=model,
                provider=provider,
                rpc_url=rpc_url,
                max_turns=max_turns,
            )

            progress.remove_task(task)

        return result

    result = asyncio.run(_run())

    # Display results
    if result.success:
        console.print("[bold green]SUCCESS![/bold green]")
        console.print(f"Profit: {result.final_profit} wei")
    else:
        console.print("[bold red]FAILED[/bold red]")
        if result.error:
            console.print(f"Error: {result.error}")

    console.print(f"\nTurns: {len(result.turns)}")
    console.print(f"Tool calls: {result.total_tool_calls}")
    console.print(f"Tokens: {result.total_tokens}")
    console.print(f"Duration: {result.duration_seconds:.1f}s")

    # Save results if output specified
    if output:
        output_data = {
            "success": result.success,
            "final_profit": result.final_profit,
            "final_strategy": result.final_strategy,
            "turns": len(result.turns),
            "total_tool_calls": result.total_tool_calls,
            "total_tokens": result.total_tokens,
            "duration_seconds": result.duration_seconds,
            "error": result.error,
        }
        output.write_text(json.dumps(output_data, indent=2))
        console.print(f"\nResults saved to: {output}")

    # Show strategy if successful
    if result.final_strategy:
        console.print("\n[bold]Final Strategy:[/bold]")
        console.print(result.final_strategy[:2000])
        if len(result.final_strategy) > 2000:
            console.print("... (truncated)")


@app.command()
def fetch_source(
    address: str = typer.Argument(..., help="Contract address"),
    chain_id: int = typer.Option(1, "--chain", "-c", help="Chain ID"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output directory"),
):
    """Fetch source code for a contract."""
    async def _fetch():
        from a1.tools.source_code import SourceCodeFetcher

        fetcher = SourceCodeFetcher(chain_id)
        result = await fetcher.execute(address=address)
        await fetcher.close()
        return result

    result = asyncio.run(_fetch())

    if not result.success:
        console.print(f"[red]Error: {result.error}[/red]")
        raise typer.Exit(1)

    console.print(f"[green]Contract: {result.details.get('contract_name')}[/green]")
    console.print(f"Compiler: {result.details.get('compiler_version')}")

    source_files = result.details.get("source_files", {})
    console.print(f"Files: {len(source_files)}")

    if output:
        output.mkdir(parents=True, exist_ok=True)
        for path, content in source_files.items():
            file_path = output / path.replace("/", "_")
            file_path.write_text(content)
        console.print(f"\nSaved to: {output}")
    else:
        # Print first file
        for path, content in source_files.items():
            console.print(f"\n--- {path} ---")
            console.print(content[:3000])
            if len(content) > 3000:
                console.print("... (truncated)")
            break


@app.command()
def read_state(
    address: str = typer.Argument(..., help="Contract address"),
    chain_id: int = typer.Option(1, "--chain", "-c", help="Chain ID"),
    function: str = typer.Option("auto", "--function", "-f", help="Function to call or 'auto'"),
    block: Optional[int] = typer.Option(None, "--block", "-b", help="Block number"),
):
    """Read state from a contract."""
    async def _read():
        from a1.tools.state_reader import BlockchainStateReader

        reader = BlockchainStateReader(chain_id)
        result = await reader.execute(
            address=address,
            function=function,
            block=block or "latest",
        )
        await reader.close()
        return result

    result = asyncio.run(_read())

    if not result.success:
        console.print(f"[red]Error: {result.error}[/red]")
        raise typer.Exit(1)

    console.print(result.summary)


@app.command()
def list_targets(
    dataset: str = typer.Argument("targets_custom", help="Dataset name"),
):
    """List targets from a dataset file."""
    import yaml

    dataset_path = Path(__file__).parent / "datasets" / f"{dataset}.yaml"

    if not dataset_path.exists():
        console.print(f"[red]Dataset not found: {dataset_path}[/red]")
        raise typer.Exit(1)

    with open(dataset_path) as f:
        targets = yaml.safe_load(f)

    table = Table(title=f"Targets: {dataset}")
    table.add_column("Name", style="cyan")
    table.add_column("Chain", style="green")
    table.add_column("Block", style="yellow")
    table.add_column("Addresses", style="blue")

    for target in targets:
        table.add_row(
            target.get("name", "unknown"),
            str(target.get("chain_id", 1)),
            str(target.get("block_number", "latest")),
            ", ".join(target.get("addresses", [])[:2]),
        )

    console.print(table)


@app.command()
def resolve_proxy(
    address: str = typer.Argument(..., help="Contract address"),
    chain_id: int = typer.Option(1, "--chain", "-c", help="Chain ID"),
    no_nested: bool = typer.Option(False, "--no-nested", help="Don't resolve nested proxies"),
):
    """Resolve a proxy contract to its implementation."""
    async def _resolve():
        from a1.tools.proxy_resolver import ProxyResolver

        resolver = ProxyResolver(chain_id)
        result = await resolver.execute(
            address=address,
            resolve_nested=not no_nested,
        )
        await resolver.close()
        return result

    result = asyncio.run(_resolve())

    if not result.success:
        console.print(f"[red]Error: {result.error}[/red]")
        raise typer.Exit(1)

    console.print(result.summary)


@app.command()
def extract_constructor(
    address: str = typer.Argument(..., help="Contract address"),
    chain_id: int = typer.Option(1, "--chain", "-c", help="Chain ID"),
):
    """Extract constructor parameters from a contract."""
    async def _extract():
        from a1.tools.constructor_extractor import ConstructorExtractor

        extractor = ConstructorExtractor(chain_id)
        result = await extractor.execute(address=address)
        await extractor.close()
        return result

    result = asyncio.run(_extract())

    if not result.success:
        console.print(f"[red]Error: {result.error}[/red]")
        raise typer.Exit(1)

    console.print(result.summary)


@app.command()
def analyze_code(
    source_file: Path = typer.Argument(..., help="Solidity source file"),
    target: Optional[str] = typer.Option(None, "--target", "-t", help="Target contract name"),
    minimal: bool = typer.Option(False, "--minimal", "-m", help="Extract minimal source"),
):
    """Analyze Solidity code structure and dependencies."""
    from a1.tools.code_sanitizer import CodeSanitizer, ASTAnalyzer

    if not source_file.exists():
        console.print(f"[red]File not found: {source_file}[/red]")
        raise typer.Exit(1)

    code = source_file.read_text()
    sanitizer = CodeSanitizer()

    if minimal and target:
        # Extract minimal source
        minimal_code = sanitizer.extract_minimal(code, target)
        console.print(minimal_code)
    else:
        # Analyze structure
        info = sanitizer.get_contract_info(code)

        console.print(f"[bold]Pragma:[/bold] {info.get('pragma', 'N/A')}")
        console.print(f"[bold]Imports:[/bold] {len(info.get('imports', []))}")
        console.print()

        for name, contract in info.get("contracts", {}).items():
            console.print(f"[bold cyan]{contract['type']} {name}[/bold cyan]")
            if contract.get("inherits"):
                console.print(f"  Inherits: {', '.join(contract['inherits'])}")
            console.print(f"  Functions: {len(contract.get('functions', []))}")
            console.print(f"  Events: {len(contract.get('events', []))}")
            console.print(f"  State Vars: {len(contract.get('state_variables', []))}")
            console.print(f"  Lines: {contract.get('lines', (0, 0))[0]}-{contract.get('lines', (0, 0))[1]}")
            console.print()

        if target:
            unused = sanitizer.find_unused_contracts(code, [target])
            if unused:
                console.print(f"[yellow]Unused contracts (from {target}): {', '.join(unused)}[/yellow]")


@app.command()
def experiment(
    target: str = typer.Argument(..., help="Target name from dataset"),
    model: str = typer.Option("gpt-4-turbo", "--model", "-m", help="Model name"),
    dataset: str = typer.Option("targets_custom", "--dataset", "-d", help="Dataset file"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output directory"),
    rpc_url: Optional[str] = typer.Option(None, "--rpc", help="Custom RPC URL"),
):
    """Run a single experiment on a target."""
    async def _run():
        from a1.experiments.run_one import run_single_experiment

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task(f"Running {target} with {model}...", total=None)

            result = await run_single_experiment(
                target_name=target,
                model_name=model,
                targets_file=dataset,
                output_dir=output,
                rpc_url=rpc_url,
            )

            progress.remove_task(task)

        return result

    result = asyncio.run(_run())

    if result.get("success"):
        console.print(f"[bold green]SUCCESS[/bold green] - Profit: {result['final_profit']} wei")
    else:
        console.print(f"[bold red]FAILED[/bold red] - {result.get('error', 'Unknown error')}")

    console.print(f"Turns: {result.get('turns', 0)} | Tokens: {result.get('total_tokens', 0)}")


@app.command()
def batch(
    dataset: str = typer.Option("targets_custom", "--dataset", "-d", help="Dataset file"),
    models: Optional[str] = typer.Option(None, "--models", "-m", help="Comma-separated model names"),
    targets: Optional[str] = typer.Option(None, "--targets", "-t", help="Comma-separated target names"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output directory"),
    parallel: int = typer.Option(1, "--parallel", "-p", help="Parallel experiments"),
    rpc_url: Optional[str] = typer.Option(None, "--rpc", help="Custom RPC URL"),
):
    """Run batch experiments."""
    async def _run():
        from a1.experiments.run_batch import run_batch_experiments

        target_list = targets.split(",") if targets else None
        model_list = models.split(",") if models else None

        results = await run_batch_experiments(
            targets=target_list,
            models=model_list,
            targets_file=dataset,
            output_dir=output,
            rpc_url=rpc_url,
            parallel=parallel,
        )

        return results

    results = asyncio.run(_run())

    # Summary
    successful = sum(1 for r in results if r.get("success"))
    console.print(f"\n[bold]Batch Complete:[/bold] {successful}/{len(results)} successful")


@app.command()
def metrics(
    output_dir: Path = typer.Argument(..., help="Directory with experiment results"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Calculate and display experiment metrics."""
    from a1.experiments.metrics import load_results_from_dir, calculate_metrics, format_metrics_report

    results = load_results_from_dir(output_dir)

    if not results:
        console.print("[yellow]No results found in directory[/yellow]")
        raise typer.Exit(1)

    exp_metrics = calculate_metrics(results)

    if json_output:
        import dataclasses
        console.print_json(data=dataclasses.asdict(exp_metrics))
    else:
        console.print(format_metrics_report(exp_metrics))


@app.command()
def results(
    command: str = typer.Argument("list", help="Command: list, stats, export, import"),
    path: Optional[Path] = typer.Option(None, "--path", "-p", help="File path for export/import"),
    target: Optional[str] = typer.Option(None, "--target", "-t", help="Filter by target"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Filter by model"),
    limit: int = typer.Option(20, "--limit", "-l", help="Max results for list"),
):
    """Manage stored experiment results."""
    from a1.experiments.results_store import ResultsStore

    store = ResultsStore()

    if command == "list":
        runs = store.list_runs(target=target, model=model, limit=limit)
        table = Table(title="Experiment Results")
        table.add_column("ID", style="dim")
        table.add_column("Target", style="cyan")
        table.add_column("Model", style="green")
        table.add_column("Status")
        table.add_column("Profit", justify="right")
        table.add_column("Turns", justify="right")

        for run in runs:
            status = "[green]✓[/green]" if run.success else "[red]✗[/red]"
            table.add_row(
                run.run_id[:8],
                run.target_name,
                run.model_name,
                status,
                f"{run.final_profit:,}",
                str(run.turns),
            )

        console.print(table)

    elif command == "stats":
        stats = store.get_stats()
        console.print_json(data=stats)

    elif command == "export":
        if not path:
            console.print("[red]--path required for export[/red]")
            raise typer.Exit(1)
        count = store.export_jsonl(path)
        console.print(f"Exported {count} records to {path}")

    elif command == "import":
        if not path:
            console.print("[red]--path required for import[/red]")
            raise typer.Exit(1)
        count = store.import_jsonl(path)
        console.print(f"Imported {count} records from {path}")

    else:
        console.print(f"[red]Unknown command: {command}[/red]")
        console.print("Available: list, stats, export, import")


@app.command()
def version():
    """Show version information."""
    from a1 import __version__
    console.print(f"A1-repro version {__version__}")


if __name__ == "__main__":
    app()
