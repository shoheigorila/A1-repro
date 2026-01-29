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
def version():
    """Show version information."""
    from a1 import __version__
    console.print(f"A1-repro version {__version__}")


if __name__ == "__main__":
    app()
