"""Run batch experiments."""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from a1.experiments.run_one import run_single_experiment


async def run_batch_experiments(
    targets: list[str] | None = None,
    models: list[str] | None = None,
    targets_file: str = "targets_custom",
    models_file: str = "models",
    output_dir: Path | None = None,
    rpc_url: str | None = None,
    parallel: int = 1,
) -> list[dict[str, Any]]:
    """Run batch experiments across multiple targets and models.

    Args:
        targets: List of target names (None = all targets)
        models: List of model names (None = all models)
        targets_file: Name of the targets YAML file
        models_file: Name of the models YAML file
        output_dir: Base directory for results
        rpc_url: Optional custom RPC URL
        parallel: Number of parallel experiments (default 1)

    Returns:
        List of experiment results
    """
    datasets_dir = Path(__file__).parent.parent / "datasets"

    # Load all targets
    targets_path = datasets_dir / f"{targets_file}.yaml"
    with open(targets_path) as f:
        all_targets = yaml.safe_load(f)

    if targets is None:
        targets = [t["name"] for t in all_targets]

    # Load all models
    models_path = datasets_dir / f"{models_file}.yaml"
    with open(models_path) as f:
        all_models = yaml.safe_load(f)

    if models is None:
        models = [m["name"] for m in all_models]

    # Setup output directory
    if output_dir is None:
        output_dir = Path("outputs/batch") / datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Build experiment matrix
    experiments = [
        (target, model)
        for target in targets
        for model in models
    ]

    print(f"Running {len(experiments)} experiments...")

    # Run experiments
    results = []

    if parallel == 1:
        # Sequential execution
        for i, (target, model) in enumerate(experiments):
            print(f"[{i+1}/{len(experiments)}] {target} x {model}")
            try:
                exp_output = output_dir / f"{target}_{model}"
                result = await run_single_experiment(
                    target_name=target,
                    model_name=model,
                    targets_file=targets_file,
                    models_file=models_file,
                    output_dir=exp_output,
                    rpc_url=rpc_url,
                )
                results.append(result)
                status = "SUCCESS" if result["success"] else "FAILED"
                print(f"  -> {status} (profit: {result['final_profit']}, turns: {result['turns']})")
            except Exception as e:
                print(f"  -> ERROR: {e}")
                results.append({
                    "target": {"name": target},
                    "model": {"name": model},
                    "success": False,
                    "error": str(e),
                })
    else:
        # Parallel execution
        semaphore = asyncio.Semaphore(parallel)

        async def run_with_semaphore(target: str, model: str) -> dict[str, Any]:
            async with semaphore:
                try:
                    exp_output = output_dir / f"{target}_{model}"
                    result = await run_single_experiment(
                        target_name=target,
                        model_name=model,
                        targets_file=targets_file,
                        models_file=models_file,
                        output_dir=exp_output,
                        rpc_url=rpc_url,
                    )
                    return result
                except Exception as e:
                    return {
                        "target": {"name": target},
                        "model": {"name": model},
                        "success": False,
                        "error": str(e),
                    }

        tasks = [
            run_with_semaphore(target, model)
            for target, model in experiments
        ]
        results = await asyncio.gather(*tasks)

    # Save batch summary
    summary = {
        "total_experiments": len(experiments),
        "successful": sum(1 for r in results if r.get("success")),
        "failed": sum(1 for r in results if not r.get("success")),
        "total_profit": sum(r.get("final_profit", 0) for r in results),
        "timestamp": datetime.now().isoformat(),
        "targets": targets,
        "models": models,
    }

    summary_path = output_dir / "batch_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)

    # Save all results
    results_path = output_dir / "all_results.jsonl"
    with open(results_path, "w") as f:
        for result in results:
            f.write(json.dumps(result, default=str) + "\n")

    print(f"\nBatch complete: {summary['successful']}/{summary['total_experiments']} successful")
    print(f"Results saved to: {output_dir}")

    return results


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Run batch A1 experiments")
    parser.add_argument("--targets", nargs="*", help="Target names (default: all)")
    parser.add_argument("--models", nargs="*", help="Model names (default: all)")
    parser.add_argument("--targets-file", default="targets_custom", help="Targets YAML file")
    parser.add_argument("--models-file", default="models", help="Models YAML file")
    parser.add_argument("--output", type=Path, help="Output directory")
    parser.add_argument("--rpc", help="Custom RPC URL")
    parser.add_argument("--parallel", type=int, default=1, help="Parallel experiments")

    args = parser.parse_args()

    results = asyncio.run(
        run_batch_experiments(
            targets=args.targets,
            models=args.models,
            targets_file=args.targets_file,
            models_file=args.models_file,
            output_dir=args.output,
            rpc_url=args.rpc,
            parallel=args.parallel,
        )
    )


if __name__ == "__main__":
    main()
