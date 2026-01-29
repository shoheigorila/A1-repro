"""SQLite cache for tool results."""

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from a1.config import settings


class Cache:
    """SQLite-based cache for tool results."""

    def __init__(self, cache_dir: str | None = None):
        self.cache_dir = Path(cache_dir or settings.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.cache_dir / "cache.db"
        self._init_db()

    def _init_db(self) -> None:
        """Initialize the database schema."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    ttl INTEGER NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_created_at ON cache(created_at)")
            conn.commit()

    @staticmethod
    def make_key(*args: Any, **kwargs: Any) -> str:
        """Generate a cache key from arguments."""
        data = json.dumps({"args": args, "kwargs": kwargs}, sort_keys=True, default=str)
        return hashlib.sha256(data.encode()).hexdigest()

    def get(self, key: str) -> dict[str, Any] | None:
        """Get a value from cache if not expired."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT value, created_at, ttl FROM cache WHERE key = ?",
                (key,),
            )
            row = cursor.fetchone()

            if row is None:
                return None

            value, created_at, ttl = row
            if time.time() > created_at + ttl:
                # Expired, delete and return None
                conn.execute("DELETE FROM cache WHERE key = ?", (key,))
                conn.commit()
                return None

            return json.loads(value)

    def set(self, key: str, value: dict[str, Any], ttl: int | None = None) -> None:
        """Set a value in cache."""
        ttl = ttl or settings.cache_ttl
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO cache (key, value, created_at, ttl)
                VALUES (?, ?, ?, ?)
                """,
                (key, json.dumps(value), int(time.time()), ttl),
            )
            conn.commit()

    def delete(self, key: str) -> None:
        """Delete a key from cache."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM cache WHERE key = ?", (key,))
            conn.commit()

    def clear(self) -> None:
        """Clear all cache entries."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM cache")
            conn.commit()

    def cleanup_expired(self) -> int:
        """Remove expired entries. Returns count of deleted entries."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM cache WHERE created_at + ttl < ?",
                (int(time.time()),),
            )
            conn.commit()
            return cursor.rowcount


# Global cache instance
cache = Cache()
