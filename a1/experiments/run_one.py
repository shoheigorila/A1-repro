"""Run a single experiment."""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from a1.controller.loop import run_agent, LoopResult


async def run_single_experiment(
    target_name: str,
    model_name: str,
    targets_file: str = "targets_custom",
    models_file: str = "models",
    output_dir: Path | None = None,
    rpc_url: str | None = None,
) -> dict[str, Any]:
    """Run a single experiment with specified target and model.

    Args:
        target_name: Name of the target from the targets file
        model_name: Name of the model from the models file
        targets_file: Name of the targets YAML file (without .yaml)
        models_file: Name of the models YAML file (without .yaml)
        output_dir: Directory to save results
        rpc_url: Optional custom RPC URL

    Returns:
        Dictionary with experiment results
    """
    datasets_dir = Path(__file__).parent.parent / "datasets"

    # Load target
    targets_path = datasets_dir / f"{targets_file}.yaml"
    with open(targets_path) as f:
        targets = yaml.safe_load(f)

    target = None
    for t in targets:
        if t.get("name") == target_name:
            target = t
            break

    if not target:
        raise ValueError(f"Target '{target_name}' not found in {targets_file}")

    # Load model config
    models_path = datasets_dir / f"{models_file}.yaml"
    with open(models_path) as f:
        models = yaml.safe_load(f)

    model_config = None
    for m in models:
        if m.get("name") == model_name:
            model_config = m
            break

    if not model_config:
        raise ValueError(f"Model '{model_name}' not found in {models_file}")

    # Setup output directory
    if output_dir is None:
        output_dir = Path("outputs/runs") / datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Run experiment
    target_address = target["addresses"][0]
    result = await run_agent(
        target_address=target_address,
        chain_id=target.get("chain_id", 1),
        block_number=target.get("block_number"),
        model=model_config["model"],
        provider=model_config["provider"],
        rpc_url=rpc_url,
        max_turns=model_config.get("max_turns", 5),
    )

    # Build experiment result
    experiment_result = {
        "target": target,
        "model": model_config,
        "success": result.success,
        "final_profit": result.final_profit,
        "turns": len(result.turns),
        "total_tool_calls": result.total_tool_calls,
        "total_tokens": result.total_tokens,
        "duration_seconds": result.duration_seconds,
        "error": result.error,
        "timestamp": datetime.now().isoformat(),
    }

    # Save results
    _save_results(output_dir, result, experiment_result)

    return experiment_result


def _save_results(
    output_dir: Path,
    result: LoopResult,
    experiment_result: dict[str, Any],
) -> None:
    """Save experiment results to files."""
    # Save summary
    summary_path = output_dir / "summary.json"
    with open(summary_path, "w") as f:
        json.dump(experiment_result, f, indent=2, default=str)

    # Save final strategy
    if result.final_strategy:
        strategy_path = output_dir / "strategy.sol"
        with open(strategy_path, "w") as f:
            f.write(result.final_strategy)

    # Save turn-by-turn context
    context_path = output_dir / "context_turns.jsonl"
    with open(context_path, "w") as f:
        for turn in result.turns:
            turn_data = {
                "turn": turn.turn,
                "timestamp": turn.timestamp,
                "tokens_used": turn.tokens_used,
                "tool_calls": turn.tool_calls,
                "strategy_code": turn.strategy_code[:500] if turn.strategy_code else None,
                "execution_result": {
                    k: v for k, v in (turn.execution_result or {}).items()
                    if k not in ("trace", "strategy_code")
                },
            }
            f.write(json.dumps(turn_data, default=str) + "\n")

    # Save tool calls
    tool_calls_path = output_dir / "tool_calls.jsonl"
    with open(tool_calls_path, "w") as f:
        for turn in result.turns:
            for tc in turn.tool_calls:
                tc_data = {
                    "turn": turn.turn,
                    "timestamp": turn.timestamp,
                    **tc,
                }
                f.write(json.dumps(tc_data, default=str) + "\n")

    # Save execution results
    execution_path = output_dir / "execution.jsonl"
    with open(execution_path, "w") as f:
        for turn in result.turns:
            if turn.execution_result:
                exec_data = {
                    "turn": turn.turn,
                    "compile_success": turn.execution_result.get("compile_success"),
                    "execution_success": turn.execution_result.get("execution_success"),
                    "revert_reason": turn.execution_result.get("revert_reason"),
                    "profit": turn.execution_result.get("profit"),
                    "gas_used": turn.execution_result.get("gas_used"),
                }
                f.write(json.dumps(exec_data, default=str) + "\n")


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Run a single A1 experiment")
    parser.add_argument("--target", required=True, help="Target name from targets file")
    parser.add_argument("--model", default="gpt-4-turbo", help="Model name from models file")
    parser.add_argument("--targets-file", default="targets_custom", help="Targets YAML file")
    parser.add_argument("--models-file", default="models", help="Models YAML file")
    parser.add_argument("--output", type=Path, help="Output directory")
    parser.add_argument("--rpc", help="Custom RPC URL")

    args = parser.parse_args()

    result = asyncio.run(
        run_single_experiment(
            target_name=args.target,
            model_name=args.model,
            targets_file=args.targets_file,
            models_file=args.models_file,
            output_dir=args.output,
            rpc_url=args.rpc,
        )
    )

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
