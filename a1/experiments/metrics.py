"""Experiment metrics calculation."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import json
import statistics


@dataclass
class ExperimentMetrics:
    """Metrics for a set of experiments."""
    # Basic counts
    total_experiments: int = 0
    successful: int = 0
    failed: int = 0

    # Success rate
    success_rate: float = 0.0

    # Profit metrics
    total_profit: int = 0
    mean_profit: float = 0.0
    median_profit: float = 0.0
    max_profit: int = 0
    min_profit: int = 0

    # Turn metrics
    mean_turns: float = 0.0
    median_turns: float = 0.0
    max_turns: int = 0
    min_turns: int = 0

    # Token metrics
    total_tokens: int = 0
    mean_tokens: float = 0.0

    # Time metrics
    total_duration: float = 0.0
    mean_duration: float = 0.0

    # Tool call metrics
    total_tool_calls: int = 0
    mean_tool_calls: float = 0.0

    # Cost estimation (approximate)
    estimated_cost_usd: float = 0.0

    # Per-model breakdown
    by_model: dict[str, "ExperimentMetrics"] = field(default_factory=dict)

    # Per-target breakdown
    by_target: dict[str, "ExperimentMetrics"] = field(default_factory=dict)

    # Per-difficulty breakdown
    by_difficulty: dict[str, "ExperimentMetrics"] = field(default_factory=dict)

    # Error breakdown
    error_types: dict[str, int] = field(default_factory=dict)


def calculate_metrics(results: list[dict[str, Any]]) -> ExperimentMetrics:
    """Calculate metrics from experiment results.

    Args:
        results: List of experiment result dictionaries

    Returns:
        Calculated ExperimentMetrics
    """
    if not results:
        return ExperimentMetrics()

    metrics = ExperimentMetrics()
    metrics.total_experiments = len(results)

    # Separate successful and failed
    successful_results = [r for r in results if r.get("success")]
    failed_results = [r for r in results if not r.get("success")]

    metrics.successful = len(successful_results)
    metrics.failed = len(failed_results)
    metrics.success_rate = metrics.successful / metrics.total_experiments if metrics.total_experiments > 0 else 0.0

    # Profit metrics (only from successful)
    profits = [r.get("final_profit", 0) for r in successful_results]
    if profits:
        metrics.total_profit = sum(profits)
        metrics.mean_profit = statistics.mean(profits)
        metrics.median_profit = statistics.median(profits)
        metrics.max_profit = max(profits)
        metrics.min_profit = min(profits)

    # Turn metrics (from all experiments that ran)
    turns = [r.get("turns", 0) for r in results if r.get("turns")]
    if turns:
        metrics.mean_turns = statistics.mean(turns)
        metrics.median_turns = statistics.median(turns)
        metrics.max_turns = max(turns)
        metrics.min_turns = min(turns)

    # Token metrics
    tokens = [r.get("total_tokens", 0) for r in results if r.get("total_tokens")]
    if tokens:
        metrics.total_tokens = sum(tokens)
        metrics.mean_tokens = statistics.mean(tokens)

    # Duration metrics
    durations = [r.get("duration_seconds", 0) for r in results if r.get("duration_seconds")]
    if durations:
        metrics.total_duration = sum(durations)
        metrics.mean_duration = statistics.mean(durations)

    # Tool call metrics
    tool_calls = [r.get("total_tool_calls", 0) for r in results if r.get("total_tool_calls")]
    if tool_calls:
        metrics.total_tool_calls = sum(tool_calls)
        metrics.mean_tool_calls = statistics.mean(tool_calls)

    # Estimate cost (rough approximation)
    metrics.estimated_cost_usd = _estimate_cost(results)

    # Group by model
    models: dict[str, list[dict]] = {}
    for r in results:
        model_name = r.get("model", {}).get("name", "unknown")
        if model_name not in models:
            models[model_name] = []
        models[model_name].append(r)

    for model_name, model_results in models.items():
        metrics.by_model[model_name] = _calculate_group_metrics(model_results)

    # Group by target
    targets: dict[str, list[dict]] = {}
    for r in results:
        target_name = r.get("target", {}).get("name", "unknown")
        if target_name not in targets:
            targets[target_name] = []
        targets[target_name].append(r)

    for target_name, target_results in targets.items():
        metrics.by_target[target_name] = _calculate_group_metrics(target_results)

    # Group by difficulty
    difficulties: dict[str, list[dict]] = {}
    for r in results:
        difficulty = r.get("target", {}).get("difficulty", "unknown")
        if difficulty not in difficulties:
            difficulties[difficulty] = []
        difficulties[difficulty].append(r)

    for difficulty, diff_results in difficulties.items():
        metrics.by_difficulty[difficulty] = _calculate_group_metrics(diff_results)

    # Error breakdown
    for r in failed_results:
        error = r.get("error", "Unknown error")
        # Normalize error message
        error_type = _categorize_error(error)
        metrics.error_types[error_type] = metrics.error_types.get(error_type, 0) + 1

    return metrics


def _calculate_group_metrics(results: list[dict[str, Any]]) -> ExperimentMetrics:
    """Calculate metrics for a subset of results."""
    metrics = ExperimentMetrics()
    metrics.total_experiments = len(results)

    successful = [r for r in results if r.get("success")]
    metrics.successful = len(successful)
    metrics.failed = metrics.total_experiments - metrics.successful
    metrics.success_rate = metrics.successful / metrics.total_experiments if metrics.total_experiments > 0 else 0.0

    profits = [r.get("final_profit", 0) for r in successful]
    if profits:
        metrics.total_profit = sum(profits)
        metrics.mean_profit = statistics.mean(profits)

    turns = [r.get("turns", 0) for r in results if r.get("turns")]
    if turns:
        metrics.mean_turns = statistics.mean(turns)

    tokens = [r.get("total_tokens", 0) for r in results if r.get("total_tokens")]
    if tokens:
        metrics.total_tokens = sum(tokens)
        metrics.mean_tokens = statistics.mean(tokens)

    return metrics


def _estimate_cost(results: list[dict[str, Any]]) -> float:
    """Estimate total cost in USD.

    Rough approximation based on token counts and model pricing.
    """
    # Approximate pricing per 1M tokens (input + output combined)
    PRICING = {
        "gpt-4-turbo": 20.0,
        "gpt-4o": 10.0,
        "gpt-4": 60.0,
        "claude-3-opus": 30.0,
        "claude-3-sonnet": 6.0,
        "claude-3.5-sonnet": 6.0,
        "default": 10.0,
    }

    total_cost = 0.0

    for r in results:
        tokens = r.get("total_tokens", 0)
        model_name = r.get("model", {}).get("name", "default")

        # Find matching pricing
        price_per_m = PRICING.get("default")
        for key, price in PRICING.items():
            if key in model_name.lower():
                price_per_m = price
                break

        cost = (tokens / 1_000_000) * price_per_m
        total_cost += cost

    return total_cost


def _categorize_error(error: str) -> str:
    """Categorize an error message into a type."""
    error_lower = error.lower()

    if "compilation" in error_lower or "compiler" in error_lower:
        return "Compilation Error"
    elif "revert" in error_lower:
        return "Execution Revert"
    elif "timeout" in error_lower:
        return "Timeout"
    elif "rpc" in error_lower or "connection" in error_lower:
        return "RPC Error"
    elif "not found" in error_lower or "not verified" in error_lower:
        return "Source Not Found"
    elif "api" in error_lower or "rate limit" in error_lower:
        return "API Error"
    else:
        return "Other Error"


def load_results_from_dir(output_dir: Path) -> list[dict[str, Any]]:
    """Load all results from an output directory.

    Args:
        output_dir: Directory containing experiment results

    Returns:
        List of experiment result dictionaries
    """
    results = []

    # Check for batch results file
    all_results_path = output_dir / "all_results.jsonl"
    if all_results_path.exists():
        with open(all_results_path) as f:
            for line in f:
                if line.strip():
                    results.append(json.loads(line))
        return results

    # Otherwise, look for individual experiment directories
    for exp_dir in output_dir.iterdir():
        if exp_dir.is_dir():
            summary_path = exp_dir / "summary.json"
            if summary_path.exists():
                with open(summary_path) as f:
                    results.append(json.load(f))

    return results


def format_metrics_report(metrics: ExperimentMetrics) -> str:
    """Format metrics as a human-readable report.

    Args:
        metrics: Calculated metrics

    Returns:
        Formatted string report
    """
    lines = [
        "=" * 60,
        "EXPERIMENT METRICS REPORT",
        "=" * 60,
        "",
        "## Overview",
        f"  Total Experiments: {metrics.total_experiments}",
        f"  Successful: {metrics.successful}",
        f"  Failed: {metrics.failed}",
        f"  Success Rate: {metrics.success_rate:.1%}",
        "",
        "## Profit",
        f"  Total Profit: {metrics.total_profit:,} wei",
        f"  Mean Profit: {metrics.mean_profit:,.0f} wei",
        f"  Median Profit: {metrics.median_profit:,.0f} wei",
        f"  Max Profit: {metrics.max_profit:,} wei",
        "",
        "## Turns",
        f"  Mean Turns: {metrics.mean_turns:.2f}",
        f"  Median Turns: {metrics.median_turns:.1f}",
        f"  Max Turns: {metrics.max_turns}",
        "",
        "## Tokens & Cost",
        f"  Total Tokens: {metrics.total_tokens:,}",
        f"  Mean Tokens: {metrics.mean_tokens:,.0f}",
        f"  Estimated Cost: ${metrics.estimated_cost_usd:.2f}",
        "",
        "## Duration",
        f"  Total Duration: {metrics.total_duration:.1f}s",
        f"  Mean Duration: {metrics.mean_duration:.1f}s",
        "",
    ]

    # By model breakdown
    if metrics.by_model:
        lines.append("## By Model")
        for model, m in metrics.by_model.items():
            lines.append(f"  {model}:")
            lines.append(f"    Success: {m.successful}/{m.total_experiments} ({m.success_rate:.0%})")
            lines.append(f"    Mean Profit: {m.mean_profit:,.0f} wei")
            lines.append(f"    Mean Turns: {m.mean_turns:.2f}")
        lines.append("")

    # By difficulty breakdown
    if metrics.by_difficulty:
        lines.append("## By Difficulty")
        for diff, m in metrics.by_difficulty.items():
            lines.append(f"  {diff}:")
            lines.append(f"    Success: {m.successful}/{m.total_experiments} ({m.success_rate:.0%})")
        lines.append("")

    # Error breakdown
    if metrics.error_types:
        lines.append("## Errors")
        for error_type, count in sorted(metrics.error_types.items(), key=lambda x: -x[1]):
            lines.append(f"  {error_type}: {count}")
        lines.append("")

    lines.append("=" * 60)

    return "\n".join(lines)


def main():
    """CLI entry point for metrics calculation."""
    import argparse

    parser = argparse.ArgumentParser(description="Calculate experiment metrics")
    parser.add_argument("output_dir", type=Path, help="Directory with experiment results")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    results = load_results_from_dir(args.output_dir)
    metrics = calculate_metrics(results)

    if args.json:
        # Convert to dict for JSON output
        import dataclasses
        metrics_dict = dataclasses.asdict(metrics)
        print(json.dumps(metrics_dict, indent=2, default=str))
    else:
        print(format_metrics_report(metrics))


if __name__ == "__main__":
    main()
