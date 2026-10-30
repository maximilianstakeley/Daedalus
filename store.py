from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import Observation


class SQLiteStore:
    """Local development/event store. PostGIS is the planned spatial production backend."""

    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS observations (
                id TEXT PRIMARY KEY,
                observed_at TEXT NOT NULL,
                ingested_at TEXT NOT NULL,
                source TEXT NOT NULL,
                kind TEXT NOT NULL,
                data_class TEXT NOT NULL,
                latitude REAL,
                longitude REAL,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_observations_time ON observations(observed_at);
            CREATE INDEX IF NOT EXISTS idx_observations_kind_time ON observations(kind, observed_at);
            CREATE INDEX IF NOT EXISTS idx_observations_source ON observations(source);

            CREATE TABLE IF NOT EXISTS simulation_runs (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                model_name TEXT NOT NULL,
                title TEXT NOT NULL,
                input_json TEXT NOT NULL,
                result_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_simulation_runs_created ON simulation_runs(created_at);
            """)

    def insert_observation(self, observation: Observation) -> bool:
        payload = observation.model_dump(mode="json")
        geo = observation.geo
        with self._conn:
            cur = self._conn.execute(
                """INSERT OR IGNORE INTO observations
                (id, observed_at, ingested_at, source, kind, data_class, latitude, longitude, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    observation.id,
                    observation.observed_at.isoformat(),
                    observation.ingested_at.isoformat(),
                    observation.source,
                    observation.kind,
                    observation.data_class.value,
                    geo.latitude if geo else None,
                    geo.longitude if geo else None,
                    json.dumps(payload, separators=(",", ":")),
                ),
            )
        return cur.rowcount == 1

    def list_observations(
        self,
        limit: int = 500,
        since: datetime | None = None,
        until: datetime | None = None,
        kind: str | None = None,
        data_class: str | None = None,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if since:
            clauses.append("observed_at >= ?")
            params.append(since.isoformat())
        if until:
            clauses.append("observed_at <= ?")
            params.append(until.isoformat())
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if data_class:
            clauses.append("data_class = ?")
            params.append(data_class)
        if source:
            clauses.append("source = ?")
            params.append(source)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._conn.execute(
            f"SELECT payload_json FROM observations{where} ORDER BY observed_at DESC, ingested_at DESC LIMIT ?",
            [*params, limit],
        ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def count_matching_observations(
        self, since: datetime | None = None, until: datetime | None = None,
        kind: str | None = None, data_class: str | None = None, source: str | None = None,
    ) -> int:
        clauses: list[str] = []
        params: list[Any] = []
        if since:
            clauses.append("observed_at >= ?")
            params.append(since.isoformat())
        if until:
            clauses.append("observed_at <= ?")
            params.append(until.isoformat())
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if data_class:
            clauses.append("data_class = ?")
            params.append(data_class)
        if source:
            clauses.append("source = ?")
            params.append(source)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return int(self._conn.execute(f"SELECT COUNT(*) FROM observations{where}", params).fetchone()[0])

    def latest_by_kind(self, kind: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT payload_json FROM observations WHERE kind=? ORDER BY observed_at DESC, ingested_at DESC LIMIT 1", (kind,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def count_observations(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0])

    def save_simulation(self, run_id: str, model_name: str, title: str,
                        input_data: dict[str, Any], result: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn:
            self._conn.execute(
                "INSERT INTO simulation_runs VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, now, model_name, title,
                 json.dumps(input_data, separators=(",", ":")),
                 json.dumps(result, separators=(",", ":"))),
            )

    def get_simulation(self, run_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM simulation_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            return None
        return {
            "id": row["id"], "created_at": row["created_at"], "model_name": row["model_name"],
            "title": row["title"], "input": json.loads(row["input_json"]),
            "result": json.loads(row["result_json"]),
        }

    def list_simulations(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id, created_at, model_name, title FROM simulation_runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        self._conn.close()
