from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from daedalus.models import DataClass, GeoPoint, Observation, WindAdvectionRequest
from daedalus.simulation import simulate_wind_advection
from daedalus.store import SQLiteStore


def make_observation(event_id: str = "unit-1") -> Observation:
    return Observation(
        id=event_id,
        source="unit-test",
        kind="test.point",
        data_class=DataClass.OBSERVED,
        observed_at=datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc),
        geo=GeoPoint(longitude=-81.3792, latitude=28.5383),
        coordinate_frame="EPSG:4326",
        values={"temperature_c": 22.0},
        units={"temperature_c": "degC"},
        quality="high",
    )


def test_observation_rejects_naive_timestamp():
    with pytest.raises(ValueError):
        Observation(
            source="test", kind="test", data_class="observed",
            observed_at=datetime(2026, 10, 30, 12, 0),
        )


def test_observation_normalizes_offset_timestamp_to_utc():
    item = Observation(
        source="test", kind="test", data_class="observed",
        observed_at=datetime.fromisoformat("2026-10-30T08:00:00-04:00"),
    )
    assert item.observed_at.isoformat() == "2026-10-30T12:00:00+00:00"




def test_observation_validity_interval_is_timezone_aware_and_ordered():
    item = Observation(
        source="test", kind="test.alert", data_class=DataClass.OBSERVED,
        observed_at=datetime.fromisoformat("2026-10-30T12:00:00-04:00"),
        valid_from=datetime.fromisoformat("2026-10-30T13:00:00-04:00"),
        valid_until=datetime.fromisoformat("2026-10-30T14:00:00-04:00"),
    )
    assert item.observed_at.hour == 16
    assert item.valid_from.hour == 17
    assert item.valid_until.hour == 18
    with pytest.raises(ValueError, match="valid_until cannot precede valid_from"):
        Observation(
            source="test", kind="test.alert", data_class=DataClass.OBSERVED,
            observed_at=datetime.fromisoformat("2026-10-30T12:00:00+00:00"),
            valid_from=datetime.fromisoformat("2026-10-30T14:00:00+00:00"),
            valid_until=datetime.fromisoformat("2026-10-30T13:00:00+00:00"),
        )


def test_geopoint_gets_explicit_wgs84_frame_and_rejects_local_frame_mismatch():
    item = Observation(
        source="test", kind="test", data_class="observed",
        observed_at=datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc),
        geo=GeoPoint(longitude=-82.0, latitude=28.0),
    )
    assert item.coordinate_frame == "EPSG:4326"
    with pytest.raises(ValueError):
        Observation(
            source="test", kind="test", data_class="observed",
            observed_at=datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc),
            geo=GeoPoint(longitude=-82.0, latitude=28.0), coordinate_frame="map",
        )


def test_sqlite_store_deduplicates_and_filters():
    store = SQLiteStore(":memory:")
    try:
        item = make_observation()
        assert store.insert_observation(item) is True
        assert store.insert_observation(item) is False
        assert store.count_observations() == 1
        rows = store.list_observations(limit=10, kind="test.point")
        assert len(rows) == 1
        assert rows[0]["geo"]["longitude"] == pytest.approx(-81.3792)
    finally:
        store.close()


def test_wind_direction_from_west_moves_particles_east():
    request = WindAdvectionRequest(
        latitude=0.0, longitude=0.0, duration_minutes=10,
        timestep_seconds=60, particle_count=1, wind_speed_mps=10.0,
        wind_from_deg=270.0, speed_sigma_fraction=0.0,
        direction_sigma_deg=0.0, random_seed=1,
    )
    result = simulate_wind_advection(request)
    start = result["trajectories"][0]["coordinates"][0]
    end = result["trajectories"][0]["coordinates"][-1]
    assert end[0] > start[0]
    assert abs(end[1] - start[1]) < 1e-6


def test_wind_scenario_is_reproducible_for_fixed_seed():
    request = WindAdvectionRequest(
        latitude=28.0, longitude=-82.0, duration_minutes=5,
        timestep_seconds=30, particle_count=4, wind_speed_mps=5.0,
        wind_from_deg=180.0, random_seed=90210,
    )
    first = simulate_wind_advection(request)
    second = simulate_wind_advection(request)
    a = [trajectory["coordinates"] for trajectory in first["trajectories"]]
    b = [trajectory["coordinates"] for trajectory in second["trajectories"]]
    assert a == b
    result = first
    assert result["data_class"] == "simulated"
    assert result["trajectories"][0]["altitude_m"] == request.release_altitude_m
    assert result["trajectories"][0]["samples"][0]["elapsed_seconds"] == 0.0
    assert result["trajectories"][0]["samples"][0]["altitude_m"] == request.release_altitude_m


def test_wind_advection_rejects_weather_forcing_from_another_region():
    request = WindAdvectionRequest(
        latitude=28.5383, longitude=-81.3792, duration_minutes=5,
        timestep_seconds=30, particle_count=2,
    )
    weather = {
        "id": "weather-tampa",
        "source": "open_meteo",
        "data_class": "model_estimate",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "quality": "medium",
        "status": "model-derived",
        "geo": {"latitude": 27.9506, "longitude": -82.4572},
        "values": {"wind_speed_10m_mps": 4.0, "wind_direction_10m_deg": 270.0},
    }
    with pytest.raises(ValueError, match="from the release point"):
        simulate_wind_advection(request, weather)


def test_wind_advection_records_local_forcing_provenance():
    request = WindAdvectionRequest(
        latitude=27.9506, longitude=-82.4572, duration_minutes=2,
        timestep_seconds=30, particle_count=1,
    )
    weather = {
        "id": "weather-tampa",
        "source": "open_meteo",
        "data_class": "model_estimate",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "quality": "medium",
        "status": "model-derived",
        "geo": {"latitude": 27.9506, "longitude": -82.4572},
        "values": {"wind_speed_10m_mps": 4.0, "wind_direction_10m_deg": 270.0},
    }
    result = simulate_wind_advection(request, weather)
    assert result["forcing"]["source"] == "open_meteo"
    assert result["forcing"]["release_to_forcing_distance_km"] == 0.0
    assert result["forcing"]["provenance"]["observation_id"] == "weather-tampa"


def test_wind_advection_rejects_stale_weather_forcing():
    request = WindAdvectionRequest(
        latitude=27.9506, longitude=-82.4572, duration_minutes=2,
        timestep_seconds=30, particle_count=1, max_forcing_age_minutes=30,
    )
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    weather = {
        "id": "stale-weather", "source": "open_meteo", "data_class": "model_estimate",
        "observed_at": old, "geo": {"latitude": 27.9506, "longitude": -82.4572},
        "values": {"wind_speed_10m_mps": 4.0, "wind_direction_10m_deg": 270.0},
    }
    with pytest.raises(ValueError, match="configured maximum"):
        simulate_wind_advection(request, weather)
