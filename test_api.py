from __future__ import annotations

import os

from fastapi.testclient import TestClient

from daedalus.app import app
from daedalus.config import settings


def test_api_health_ingest_history_and_wind_scenario(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_DISABLE_POLLING", "1")
    previous_path = settings.database_path
    previous_reconstruction_path = settings.reconstructions_dir
    settings.database_path = str(tmp_path / "api.sqlite3")
    settings.reconstructions_dir = str(tmp_path / "reconstructions")
    try:
        with TestClient(app) as client:
            recon_dir = tmp_path / "reconstructions"
            recon_dir.mkdir(parents=True, exist_ok=True)
            (recon_dir / "test_snapshot.ply").write_text("ply\nformat ascii 1.0\nelement vertex 0\nproperty float x\nproperty float y\nproperty float z\nend_header\n")
            (recon_dir / "timeline.json").write_text(__import__("json").dumps({
                "format": "daedalus-temporal-reconstruction-v1",
                "coordinate_frame": "test-local-map",
                "snapshots": [
                    {"id": "snapshot-0", "mesh": "test_snapshot.ply", "start_time": "2026-10-30T12:00:00Z", "end_time": "2026-10-30T12:00:05Z"},
                    {"id": "snapshot-escape", "mesh": "../outside.ply", "start_time": "2026-10-30T12:00:05Z", "end_time": "2026-10-30T12:00:10Z"},
                ],
            }))
            manifest = client.get("/api/reconstructions/timeline")
            assert manifest.status_code == 200
            snapshot = manifest.json()["snapshots"][0]
            assert snapshot["mesh_url"] == "/reconstructions/test_snapshot.ply"
            assert snapshot["asset_status"] == "available"
            unsafe_snapshot = manifest.json()["snapshots"][1]
            assert unsafe_snapshot["mesh_url"] is None
            assert unsafe_snapshot["asset_status"] == "invalid_asset_path"

            health = client.get("/api/health")
            assert health.status_code == 200
            assert health.json()["status"] == "ok"

            observation = {
                "id": "api-test-observation",
                "source": "api-test",
                "kind": "test.position",
                "data_class": "observed",
                "observed_at": "2026-10-30T12:00:00Z",
                "valid_from": "2026-10-30T12:00:00Z",
                "valid_until": "2026-10-30T13:00:00Z",
                "geo": {"longitude": -81.3792, "latitude": 28.5383},
                "coordinate_frame": "EPSG:4326",
                "values": {"signal": 1.0},
                "units": {"signal": "arbitrary"},
                "quality": "high",
            }
            posted = client.post("/api/observations", json=observation)
            assert posted.status_code == 201
            assert posted.json()["created"] is True

            history = client.get("/api/observations?kind=test.position")
            assert history.status_code == 200
            assert len(history.json()["items"]) == 1
            assert history.json()["count"] == 1
            # Query offsets are normalized before comparing against UTC ISO text in SQLite.
            offset_history = client.get("/api/observations?since=2026-10-30T08:00:00-04:00&until=2026-10-30T08:01:00-04:00")
            assert offset_history.status_code == 200
            assert len(offset_history.json()["items"]) == 1
            bad_range = client.get("/api/observations?since=2026-10-30T13:00:00Z&until=2026-10-30T12:00:00Z")
            assert bad_range.status_code == 422

            geojson = client.get("/api/observations.geojson")
            assert geojson.status_code == 200
            assert geojson.json()["features"][0]["geometry"]["type"] == "Point"
            assert geojson.json()["features"][0]["properties"]["valid_from"] == "2026-10-30T12:00:00Z"
            assert geojson.json()["features"][0]["properties"]["valid_until"] == "2026-10-30T13:00:00Z"

            # Local ROS/map geometry is valid simulation data but must not be
            # mislabeled as WGS84 during GeoJSON export.
            local_geometry = {
                "id": "api-test-local-geometry",
                "source": "ros2-test",
                "kind": "reconstruction.mesh_anchor",
                "data_class": "observed",
                "observed_at": "2026-10-30T12:00:00Z",
                "coordinate_frame": "daedalus-local-map",
                "geometry": {"type": "Point", "coordinates": [1.0, 2.0, 3.0]},
                "values": {"label": "local frame point"},
            }
            local_posted = client.post("/api/observations", json=local_geometry)
            assert local_posted.status_code == 201
            geojson_after_local = client.get("/api/observations.geojson").json()
            assert len(geojson_after_local["features"]) == 1
            assert geojson_after_local["features"][0]["id"] == "api-test-observation"


            # Current dashboard state is specific to its selected region. A prior
            # region's weather/forecast/alert records remain in the archive but
            # must not leak into the active regional view.
            weather_current = {
                "id": "weather-selected-region", "source": "open_meteo", "kind": "weather.current",
                "data_class": "model_estimate", "observed_at": "2026-10-30T12:00:00Z",
                "coordinate_frame": "EPSG:4326", "geo": {"longitude": -82.4572, "latitude": 27.9506},
                "values": {"wind_speed_10m_mps": 4.0},
                "metadata": {"requested_location": {"latitude": 27.9506, "longitude": -82.4572}},
            }
            weather_other_region = {
                **weather_current, "id": "weather-other-region",
                "metadata": {"requested_location": {"latitude": 28.5383, "longitude": -81.3792}},
            }
            client.post("/api/observations", json=weather_current)
            client.post("/api/observations", json=weather_other_region)
            region_state = client.get("/api/state").json()
            assert region_state["weather"]["id"] == "weather-selected-region"

            # A world-state snapshot is evaluated against one explicit simulation
            # clock and excludes observations that are expired or too old.
            snapshot = client.get("/api/world-state?as_of=2026-10-30T12:30:00Z&max_age_seconds=3600")
            assert snapshot.status_code == 200
            snapshot_data = snapshot.json()
            assert snapshot_data["integrity"] == "time_filtered_snapshot"
            assert snapshot_data["active_count"] >= 1
            assert all(item["age_seconds"] <= 3600 for item in snapshot_data["active"])

            expired_observation = {
                **weather_current, "id": "expired-at-snapshot", "kind": "test.expired",
                "observed_at": "2026-10-30T12:00:00Z", "valid_until": "2026-10-30T12:10:00Z",
            }
            client.post("/api/observations", json=expired_observation)
            snapshot = client.get("/api/world-state?as_of=2026-10-30T12:30:00Z&max_age_seconds=3600").json()
            assert "expired-at-snapshot" not in {item["id"] for item in snapshot["active"]}
            assert any(item["id"] == "expired-at-snapshot" and item["reason"] == "expired"
                       for item in snapshot["excluded"])
            stale_observation = {
                **weather_current, "id": "stale-at-snapshot", "kind": "test.stale",
                "observed_at": "2026-10-30T09:00:00Z", "valid_until": None,
            }
            client.post("/api/observations", json=stale_observation)
            snapshot = client.get("/api/world-state?as_of=2026-10-30T12:30:00Z&max_age_seconds=3600").json()
            assert any(item["id"] == "stale-at-snapshot" and item["reason"] == "stale"
                       for item in snapshot["excluded"])

            forecast_selected = {
                **weather_current, "id": "forecast-selected-region", "kind": "weather.forecast",
                "data_class": "forecast", "observed_at": "2026-10-30T13:00:00Z",
                "valid_until": "2026-10-30T14:00:00Z",
            }
            forecast_other = {
                **weather_other_region, "id": "forecast-other-region", "kind": "weather.forecast",
                "data_class": "forecast", "observed_at": "2026-10-30T13:00:00Z",
                "valid_until": "2026-10-30T14:00:00Z",
            }
            client.post("/api/observations", json=forecast_selected)
            client.post("/api/observations", json=forecast_other)
            region_state = client.get("/api/state").json()
            assert [item["id"] for item in region_state["forecast"]] == ["forecast-selected-region"]

            alert_selected = {
                **weather_current, "id": "alert-selected-region", "kind": "hazard.weather_alert",
                "values": {"event": "Test alert"}, "valid_until": "2026-10-30T18:00:00Z",
            }
            alert_other = {
                **weather_other_region, "id": "alert-other-region", "kind": "hazard.weather_alert",
                "values": {"event": "Other-region alert"}, "valid_until": "2026-10-30T18:00:00Z",
            }
            client.post("/api/observations", json=alert_selected)
            client.post("/api/observations", json=alert_other)
            region_state = client.get("/api/state").json()
            assert [item["id"] for item in region_state["alerts"]] == ["alert-selected-region"]

            simulation = client.post("/api/simulations/wind-advection", json={
                "latitude": 28.0, "longitude": -82.0, "duration_minutes": 3,
                "timestep_seconds": 30, "particle_count": 4,
                "wind_speed_mps": 6.0, "wind_from_deg": 270.0,
            })
            assert simulation.status_code == 200
            run_id = simulation.json()["id"]
            saved = client.get(f"/api/simulations/{run_id}")
            assert saved.status_code == 200
            assert len(saved.json()["result"]["trajectories"]) == 4
            assert saved.json()["result"]["data_class"] == "simulated"
            assert saved.json()["result"]["trajectories"][0]["altitude_m"] == 10.0
    finally:
        settings.database_path = previous_path
        settings.reconstructions_dir = previous_reconstruction_path
