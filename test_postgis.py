from __future__ import annotations

import asyncio
import json

from daedalus.postgis import PostGISMirror


class FakeConnection:
    def __init__(self):
        self.calls = []

    async def execute(self, query, params):
        self.calls.append((query, params))


def test_local_frame_geojson_is_not_written_as_srid_4326():
    mirror = PostGISMirror("postgresql://unused")
    mirror.connection = FakeConnection()
    payload = {
        "id": "local-frame-1",
        "source": "test",
        "kind": "pose", 
        "data_class": "observed",
        "observed_at": "2026-10-30T12:00:00+00:00",
        "ingested_at": "2026-10-30T12:00:01+00:00",
        "coordinate_frame": "map",
        "geometry": {"type": "Point", "coordinates": [1, 2, 3]},
    }
    asyncio.run(mirror.insert(payload))
    assert len(mirror.connection.calls) == 1
    query, params = mirror.connection.calls[0]
    assert "ST_GeomFromGeoJSON" not in query
    assert "payload)" in query
    assert "geom" not in query
    stored_payload = json.loads(params[-1])
    assert stored_payload["geometry"] == payload["geometry"]
