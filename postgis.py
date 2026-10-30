from __future__ import annotations

import json
from typing import Any


class PostGISMirror:
    """Optional asynchronous mirror. SQLite remains the default local event store."""
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.connection = None
        self.error: str | None = None

    async def connect(self) -> None:
        if not self.dsn:
            return
        try:
            import psycopg
            self.connection = await psycopg.AsyncConnection.connect(self.dsn, autocommit=True)
            await self.connection.execute("CREATE EXTENSION IF NOT EXISTS postgis")
            await self.connection.execute("""
                CREATE TABLE IF NOT EXISTS daedalus_observations (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    data_class TEXT NOT NULL,
                    observed_at TIMESTAMPTZ NOT NULL,
                    ingested_at TIMESTAMPTZ NOT NULL,
                    geom geometry(Geometry, 4326),
                    payload JSONB NOT NULL
                )
            """)
            await self.connection.execute("CREATE INDEX IF NOT EXISTS daedalus_observations_geom_gix ON daedalus_observations USING GIST (geom)")
            await self.connection.execute("CREATE INDEX IF NOT EXISTS daedalus_observations_time_idx ON daedalus_observations (observed_at DESC)")
            self.error = None
        except Exception as exc:
            self.error = str(exc)
            self.connection = None

    async def insert(self, observation: dict[str, Any]) -> None:
        if self.connection is None:
            return
        try:
            geo = observation.get("geo")
            geometry = observation.get("geometry")
            coordinate_frame = observation.get("coordinate_frame")
            # The mirror geometry column is fixed to SRID 4326. Never assign that
            # SRID to local ROS map/odom coordinates or another untransformed frame.
            if coordinate_frame not in {"EPSG:4326", "EPSG:4979", "WGS84"}:
                geometry = None
            payload = json.dumps(observation, separators=(",", ":"))
            if geometry:
                geom_sql = "ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)"
                geom_value = json.dumps(geometry)
                await self.connection.execute(
                    f"""INSERT INTO daedalus_observations
                    (id, source, kind, data_class, observed_at, ingested_at, geom, payload)
                    VALUES (%s,%s,%s,%s,%s,%s,{geom_sql},%s::jsonb)
                    ON CONFLICT (id) DO NOTHING""",
                    (observation["id"], observation["source"], observation["kind"], observation["data_class"],
                     observation["observed_at"], observation["ingested_at"], geom_value, payload),
                )
            elif geo:
                await self.connection.execute(
                    """INSERT INTO daedalus_observations
                    (id, source, kind, data_class, observed_at, ingested_at, geom, payload)
                    VALUES (%s,%s,%s,%s,%s,%s,ST_SetSRID(ST_MakePoint(%s,%s),4326),%s::jsonb)
                    ON CONFLICT (id) DO NOTHING""",
                    (observation["id"], observation["source"], observation["kind"], observation["data_class"],
                     observation["observed_at"], observation["ingested_at"], geo["longitude"], geo["latitude"], payload),
                )
            else:
                await self.connection.execute(
                    """INSERT INTO daedalus_observations
                    (id, source, kind, data_class, observed_at, ingested_at, payload)
                    VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)
                    ON CONFLICT (id) DO NOTHING""",
                    (observation["id"], observation["source"], observation["kind"], observation["data_class"],
                     observation["observed_at"], observation["ingested_at"], payload),
                )
            self.error = None
        except Exception as exc:
            self.error = str(exc)

    async def close(self) -> None:
        if self.connection is not None:
            await self.connection.close()
            self.connection = None
