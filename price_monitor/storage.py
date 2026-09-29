"""История цен в SQLite. Запросы выполняются в отдельном потоке, чтобы не блокировать цикл asyncio."""
from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    url       TEXT NOT NULL,
    name      TEXT NOT NULL,
    title     TEXT,
    price     REAL NOT NULL,
    currency  TEXT,
    in_stock  INTEGER,
    seen_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_obs_url_time ON observations(url, seen_at);
"""


@dataclass(frozen=True)
class Observation:
    url: str
    name: str
    title: str | None
    price: float
    currency: str | None
    in_stock: bool | None
    seen_at: datetime


class Storage:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._lock = asyncio.Lock()

    def close(self) -> None:
        self._conn.close()

    async def _run(self, fn, *args):
        async with self._lock:
            return await asyncio.to_thread(fn, *args)

    @staticmethod
    def _row(r: sqlite3.Row) -> Observation:
        return Observation(r["url"], r["name"], r["title"], r["price"], r["currency"],
                           None if r["in_stock"] is None else bool(r["in_stock"]), datetime.fromisoformat(r["seen_at"]))

    def _last(self, url: str) -> Observation | None:
        r = self._conn.execute("SELECT * FROM observations WHERE url=? ORDER BY seen_at DESC, id DESC LIMIT 1", (url,)).fetchone()
        return self._row(r) if r else None

    async def last(self, url: str) -> Observation | None:
        return await self._run(self._last, url)

    def _add(self, obs: Observation) -> None:
        self._conn.execute(
            "INSERT INTO observations(url, name, title, price, currency, in_stock, seen_at) VALUES (?,?,?,?,?,?,?)",
            (obs.url, obs.name, obs.title, obs.price, obs.currency, None if obs.in_stock is None else int(obs.in_stock),
             obs.seen_at.astimezone(timezone.utc).isoformat(timespec="seconds")))
        self._conn.commit()

    async def add(self, obs: Observation) -> None:
        await self._run(self._add, obs)

    def _history(self, url: str, since: datetime) -> list[Observation]:
        rows = self._conn.execute("SELECT * FROM observations WHERE url=? AND seen_at>=? ORDER BY seen_at",
                                  (url, since.astimezone(timezone.utc).isoformat(timespec="seconds"))).fetchall()
        return [self._row(r) for r in rows]

    async def history(self, url: str, days: int = 30, now: datetime | None = None) -> list[Observation]:
        now = now or datetime.now(timezone.utc)
        return await self._run(self._history, url, now - timedelta(days=days))
