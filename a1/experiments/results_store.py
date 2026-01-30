"""Results storage and retrieval for experiments."""

import json
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator
import hashlib


@dataclass
class RunSummary:
    """Summary of a single experiment run."""
    run_id: str
    target_name: str
    model_name: str
    chain_id: int
    block_number: int | None

    # Results
    success: bool
    final_profit: int
    turns: int
    total_tool_calls: int
    total_tokens: int
    duration_seconds: float
    error: str | None

    # Metadata
    timestamp: str
    output_dir: str
    tags: list[str]


class ResultsStore:
    """SQLite-based storage for experiment results."""

    def __init__(self, db_path: Path | str = "outputs/results.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    target_name TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    chain_id INTEGER NOT NULL,
                    block_number INTEGER,
                    success INTEGER NOT NULL,
                    final_profit INTEGER NOT NULL,
                    turns INTEGER NOT NULL,
                    total_tool_calls INTEGER NOT NULL,
                    total_tokens INTEGER NOT NULL,
                    duration_seconds REAL NOT NULL,
                    error TEXT,
                    timestamp TEXT NOT NULL,
                    output_dir TEXT,
                    tags TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS run_details (
                    run_id TEXT NOT NULL,
                    turn INTEGER NOT NULL,
                    data TEXT NOT NULL,
                    PRIMARY KEY (run_id, turn),
                    FOREIGN KEY (run_id) REFERENCES runs(run_id)
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_runs_target ON runs(target_name)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_runs_model ON runs(model_name)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_runs_success ON runs(success)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_runs_timestamp ON runs(timestamp)
            """)

            conn.commit()

    def _generate_run_id(self, target: str, model: str, timestamp: str) -> str:
        """Generate a unique run ID."""
        data = f"{target}:{model}:{timestamp}"
        return hashlib.sha256(data.encode()).hexdigest()[:16]

    def store(self, result: dict[str, Any], turn_details: list[dict] | None = None) -> str:
        """Store an experiment result.

        Args:
            result: Experiment result dictionary
            turn_details: Optional list of per-turn details

        Returns:
            Generated run_id
        """
        target = result.get("target", {})
        model = result.get("model", {})
        timestamp = result.get("timestamp", datetime.now().isoformat())

        run_id = self._generate_run_id(
            target.get("name", "unknown"),
            model.get("name", "unknown"),
            timestamp,
        )

        tags = target.get("tags", [])
        tags_json = json.dumps(tags)

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO runs (
                    run_id, target_name, model_name, chain_id, block_number,
                    success, final_profit, turns, total_tool_calls, total_tokens,
                    duration_seconds, error, timestamp, output_dir, tags
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                run_id,
                target.get("name", "unknown"),
                model.get("name", "unknown"),
                target.get("chain_id", 1),
                target.get("block_number"),
                1 if result.get("success") else 0,
                result.get("final_profit", 0),
                result.get("turns", 0),
                result.get("total_tool_calls", 0),
                result.get("total_tokens", 0),
                result.get("duration_seconds", 0),
                result.get("error"),
                timestamp,
                str(result.get("output_dir", "")),
                tags_json,
            ))

            # Store turn details if provided
            if turn_details:
                for turn in turn_details:
                    conn.execute("""
                        INSERT OR REPLACE INTO run_details (run_id, turn, data)
                        VALUES (?, ?, ?)
                    """, (run_id, turn.get("turn", 0), json.dumps(turn, default=str)))

            conn.commit()

        return run_id

    def get(self, run_id: str) -> RunSummary | None:
        """Get a run by ID."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM runs WHERE run_id = ?",
                (run_id,)
            ).fetchone()

            if not row:
                return None

            return self._row_to_summary(row)

    def get_details(self, run_id: str) -> list[dict]:
        """Get turn-by-turn details for a run."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT data FROM run_details WHERE run_id = ? ORDER BY turn",
                (run_id,)
            ).fetchall()

            return [json.loads(row[0]) for row in rows]

    def list_runs(
        self,
        target: str | None = None,
        model: str | None = None,
        success: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[RunSummary]:
        """List runs with optional filters."""
        conditions = []
        params: list[Any] = []

        if target:
            conditions.append("target_name = ?")
            params.append(target)

        if model:
            conditions.append("model_name = ?")
            params.append(model)

        if success is not None:
            conditions.append("success = ?")
            params.append(1 if success else 0)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        query = f"""
            SELECT * FROM runs
            WHERE {where_clause}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, params).fetchall()

            return [self._row_to_summary(row) for row in rows]

    def get_all_results(self) -> list[dict[str, Any]]:
        """Get all results as dictionaries for metrics calculation."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM runs ORDER BY timestamp").fetchall()

            results = []
            for row in rows:
                tags = json.loads(row["tags"]) if row["tags"] else []
                results.append({
                    "target": {
                        "name": row["target_name"],
                        "chain_id": row["chain_id"],
                        "block_number": row["block_number"],
                        "tags": tags,
                    },
                    "model": {
                        "name": row["model_name"],
                    },
                    "success": bool(row["success"]),
                    "final_profit": row["final_profit"],
                    "turns": row["turns"],
                    "total_tool_calls": row["total_tool_calls"],
                    "total_tokens": row["total_tokens"],
                    "duration_seconds": row["duration_seconds"],
                    "error": row["error"],
                    "timestamp": row["timestamp"],
                })

            return results

    def get_stats(self) -> dict[str, Any]:
        """Get aggregate statistics."""
        with sqlite3.connect(self.db_path) as conn:
            # Total counts
            total = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
            successful = conn.execute("SELECT COUNT(*) FROM runs WHERE success = 1").fetchone()[0]

            # Profit stats
            profit_stats = conn.execute("""
                SELECT
                    SUM(final_profit) as total_profit,
                    AVG(final_profit) as avg_profit,
                    MAX(final_profit) as max_profit
                FROM runs WHERE success = 1
            """).fetchone()

            # Token stats
            token_stats = conn.execute("""
                SELECT
                    SUM(total_tokens) as total_tokens,
                    AVG(total_tokens) as avg_tokens
                FROM runs
            """).fetchone()

            # By model
            by_model = {}
            rows = conn.execute("""
                SELECT model_name, COUNT(*) as total, SUM(success) as successes
                FROM runs GROUP BY model_name
            """).fetchall()
            for row in rows:
                by_model[row[0]] = {
                    "total": row[1],
                    "successful": row[2],
                    "success_rate": row[2] / row[1] if row[1] > 0 else 0,
                }

            return {
                "total_runs": total,
                "successful_runs": successful,
                "success_rate": successful / total if total > 0 else 0,
                "total_profit": profit_stats[0] or 0,
                "avg_profit": profit_stats[1] or 0,
                "max_profit": profit_stats[2] or 0,
                "total_tokens": token_stats[0] or 0,
                "avg_tokens": token_stats[1] or 0,
                "by_model": by_model,
            }

    def export_jsonl(self, output_path: Path) -> int:
        """Export all results to JSONL file.

        Returns:
            Number of records exported
        """
        results = self.get_all_results()

        with open(output_path, "w") as f:
            for result in results:
                f.write(json.dumps(result, default=str) + "\n")

        return len(results)

    def import_jsonl(self, input_path: Path) -> int:
        """Import results from JSONL file.

        Returns:
            Number of records imported
        """
        count = 0

        with open(input_path) as f:
            for line in f:
                if line.strip():
                    result = json.loads(line)
                    self.store(result)
                    count += 1

        return count

    def _row_to_summary(self, row: sqlite3.Row) -> RunSummary:
        """Convert database row to RunSummary."""
        tags = json.loads(row["tags"]) if row["tags"] else []

        return RunSummary(
            run_id=row["run_id"],
            target_name=row["target_name"],
            model_name=row["model_name"],
            chain_id=row["chain_id"],
            block_number=row["block_number"],
            success=bool(row["success"]),
            final_profit=row["final_profit"],
            turns=row["turns"],
            total_tool_calls=row["total_tool_calls"],
            total_tokens=row["total_tokens"],
            duration_seconds=row["duration_seconds"],
            error=row["error"],
            timestamp=row["timestamp"],
            output_dir=row["output_dir"],
            tags=tags,
        )

    def delete_run(self, run_id: str) -> bool:
        """Delete a run and its details."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM run_details WHERE run_id = ?", (run_id,))
            result = conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
            conn.commit()
            return result.rowcount > 0

    def clear_all(self) -> int:
        """Clear all stored results.

        Returns:
            Number of runs deleted
        """
        with sqlite3.connect(self.db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
            conn.execute("DELETE FROM run_details")
            conn.execute("DELETE FROM runs")
            conn.commit()
            return count


def main():
    """CLI entry point for results store operations."""
    import argparse

    parser = argparse.ArgumentParser(description="Manage experiment results")
    subparsers = parser.add_subparsers(dest="command")

    # List command
    list_parser = subparsers.add_parser("list", help="List runs")
    list_parser.add_argument("--target", help="Filter by target")
    list_parser.add_argument("--model", help="Filter by model")
    list_parser.add_argument("--success", type=lambda x: x.lower() == "true", help="Filter by success")
    list_parser.add_argument("--limit", type=int, default=20, help="Max results")

    # Stats command
    subparsers.add_parser("stats", help="Show statistics")

    # Export command
    export_parser = subparsers.add_parser("export", help="Export to JSONL")
    export_parser.add_argument("output", type=Path, help="Output file")

    # Import command
    import_parser = subparsers.add_parser("import", help="Import from JSONL")
    import_parser.add_argument("input", type=Path, help="Input file")

    args = parser.parse_args()
    store = ResultsStore()

    if args.command == "list":
        runs = store.list_runs(
            target=args.target,
            model=args.model,
            success=args.success,
            limit=args.limit,
        )
        for run in runs:
            status = "✓" if run.success else "✗"
            print(f"{status} {run.run_id[:8]} | {run.target_name} | {run.model_name} | profit={run.final_profit}")

    elif args.command == "stats":
        stats = store.get_stats()
        print(json.dumps(stats, indent=2))

    elif args.command == "export":
        count = store.export_jsonl(args.output)
        print(f"Exported {count} records to {args.output}")

    elif args.command == "import":
        count = store.import_jsonl(args.input)
        print(f"Imported {count} records from {args.input}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
