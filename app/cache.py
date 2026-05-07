import asyncio
import json
import sqlite3
import time
import logging
from pathlib import Path
from typing import Optional, Any

logger = logging.getLogger(__name__)


class DiskCache:
    """Persistent on-disk cache with TTL and size-based eviction."""

    def __init__(
        self,
        db_path: str = "./cache/scraper.db",
        max_size_gb: float = 10.0,
        ttl_seconds: int = 3600,
    ):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.max_size_bytes = int(max_size_gb * 1024 * 1024 * 1024)
        self.ttl_seconds = ttl_seconds
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_created_at ON cache(created_at)"
            )

    def _get_sync(self, key: str) -> Optional[Any]:
        with sqlite3.connect(str(self.db_path)) as conn:
            row = conn.execute(
                "SELECT value, created_at FROM cache WHERE key = ?",
                (key,),
            ).fetchone()
            if not row:
                return None

            value, created_at = row
            if time.time() - created_at > self.ttl_seconds:
                conn.execute("DELETE FROM cache WHERE key = ?", (key,))
                conn.commit()
                return None

            return json.loads(value)

    def _set_sync(self, key: str, value: Any) -> None:
        serialized = json.dumps(value, default=str)
        size = len(serialized.encode("utf-8"))
        now = int(time.time())

        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """INSERT INTO cache (key, value, size, created_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET
                   value=excluded.value,
                   size=excluded.size,
                   created_at=excluded.created_at""",
                (key, serialized, size, now),
            )
            self._enforce_limit(conn)
            conn.commit()

    def _enforce_limit(self, conn: sqlite3.Connection) -> None:
        total = conn.execute(
            "SELECT COALESCE(SUM(size), 0) FROM cache"
        ).fetchone()[0]

        while total > self.max_size_bytes:
            oldest = conn.execute(
                "SELECT key, size FROM cache ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
            if not oldest:
                break
            conn.execute("DELETE FROM cache WHERE key = ?", (oldest[0],))
            total -= oldest[1]

    async def get(self, key: str) -> Optional[Any]:
        return await asyncio.to_thread(self._get_sync, key)

    async def set(self, key: str, value: Any) -> None:
        await asyncio.to_thread(self._set_sync, key, value)
