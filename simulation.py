from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from .models import WindAdvectionRequest

EARTH_RADIUS_M = 6_371_000.0


def _meters_to_degrees(east_m: float, north_m: float, latitude_deg: float) -> tuple[float, float]:
    lat_delta = math.degrees(north_m / EARTH_RADIUS_M)
    cos_lat = max(1e-6, abs(math.cos(math.radians(latitude_deg))))
    lon_delta = math.degrees(east_m / (EARTH_RADIUS_M * cos_lat))
    return lat_delta, lon_delta


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(min(1.0, a)))


def simulate_wind_advection(
    request: WindAdvectionRequest,
    weather: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ensemble passive-tracer advection using locally uniform wind; not a weather forecast model."""
    if request.wind_speed_mps is not None:
        base_speed = request.wind_speed_mps
        wind_from = request.wind_from_deg
        forcing_source = "operator_supplied"
        forcing_provenance = {"data_class": "operator_input", "source": "operator_supplied", "observation_id": None}
    else:
        if not weather:
            raise ValueError("No current weather model estimate is stored. Refresh weather or supply wind explicitly.")
        values = weather.get("values", {})
        observed_at_raw = weather.get("observed_at")
        if not observed_at_raw:
            raise ValueError("Weather forcing has no observed_at timestamp; refresh weather or supply explicit wind forcing.")
        try:
            observed_at = datetime.fromisoformat(str(observed_at_raw).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Weather forcing has an invalid observed_at timestamp.") from exc
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("Weather forcing timestamp must include a timezone.")
        observed_at = observed_at.astimezone(timezone.utc)
        forcing_age_seconds = (datetime.now(timezone.utc) - observed_at).total_seconds()
        if forcing_age_seconds < -300:
            raise ValueError("Weather forcing timestamp is more than five minutes in the future; refusing simulation.")
        max_age_seconds = request.max_forcing_age_minutes * 60
        if forcing_age_seconds > max_age_seconds:
            raise ValueError(
                f"Weather forcing is {forcing_age_seconds / 60:.1f} minutes old; "
                f"the configured maximum is {request.max_forcing_age_minutes} minutes. "
                "Refresh weather or supply explicit wind forcing."
            )
        values = weather.get("values", {})
        speed_value = values.get("wind_speed_10m_mps")
        direction_value = values.get("wind_direction_10m_deg")
        if speed_value is None or direction_value is None:
            raise ValueError("The latest weather estimate does not contain both wind speed and direction")
        base_speed = float(speed_value)
        wind_from = float(direction_value)
        forcing_source = weather.get("source", "unknown")
        weather_geo = weather.get("geo") or {}
        forcing_distance_km = None
        if weather_geo.get("latitude") is not None and weather_geo.get("longitude") is not None:
            forcing_distance_km = _haversine_km(
                request.latitude, request.longitude,
                float(weather_geo["latitude"]), float(weather_geo["longitude"]),
            )
            if forcing_distance_km > 50.0:
                raise ValueError(
                    f"The latest weather forcing is {forcing_distance_km:.1f} km from the release point. "
                    "Update the selected region and refresh weather, or supply explicit wind forcing."
                )
        forcing_provenance = {
            "data_class": weather.get("data_class", "unknown"),
            "source": weather.get("source", "unknown"),
            "observation_id": weather.get("id"),
            "source_record_id": weather.get("source_record_id"),
            "observed_at": weather.get("observed_at"),
            "ingested_at": weather.get("ingested_at"),
            "quality": weather.get("quality", "unknown"),
            "status": weather.get("status", "unknown"),
            "forcing_location": weather_geo or None,
            "release_to_forcing_distance_km": round(forcing_distance_km, 3) if forcing_distance_km is not None else None,
            "forcing_age_seconds": round(max(0.0, forcing_age_seconds), 1),
            "max_forcing_age_minutes": request.max_forcing_age_minutes,
        }

    assert wind_from is not None
    rng = random.Random(request.random_seed)
    created = datetime.now(timezone.utc)
    steps = math.ceil(request.duration_minutes * 60 / request.timestep_seconds)
    trajectories: list[dict[str, Any]] = []

    for particle_index in range(request.particle_count):
        lat, lon = request.latitude, request.longitude
        # `coordinates` remains [longitude, latitude, elapsed_seconds] for the
        # current browser client; altitude is carried separately to avoid treating
        # elapsed time as a GeoJSON height coordinate.
        points = [[round(lon, 7), round(lat, 7), 0.0]]
        samples = [{
            "time": created.isoformat(), "elapsed_seconds": 0.0,
            "longitude": round(lon, 7), "latitude": round(lat, 7),
            "altitude_m": request.release_altitude_m,
        }]
        # Each ensemble member gets a persistent bias plus stepwise perturbations.
        speed_bias = rng.gauss(0.0, request.speed_sigma_fraction)
        direction_bias = rng.gauss(0.0, request.direction_sigma_deg)
        for step in range(1, steps + 1):
            elapsed_before = (step - 1) * request.timestep_seconds
            step_seconds = min(request.timestep_seconds, request.duration_minutes * 60 - elapsed_before)
            elapsed_s = elapsed_before + step_seconds
            speed = max(0.0, base_speed * (1.0 + speed_bias + rng.gauss(0.0, request.speed_sigma_fraction * 0.15)))
            from_dir = (wind_from + direction_bias + rng.gauss(0.0, request.direction_sigma_deg * 0.15)) % 360.0
            # Meteorological direction is where wind comes FROM; reverse it to get motion.
            theta = math.radians(from_dir)
            east_mps = -speed * math.sin(theta)
            north_mps = -speed * math.cos(theta)
            lat_step, lon_step = _meters_to_degrees(
                east_mps * step_seconds, north_mps * step_seconds, lat
            )
            lat = max(-89.999, min(89.999, lat + lat_step))
            lon = ((lon + lon_step + 180.0) % 360.0) - 180.0
            points.append([round(lon, 7), round(lat, 7), round(elapsed_s, 1)])
            samples.append({
                "time": (created + timedelta(seconds=elapsed_s)).isoformat(),
                "elapsed_seconds": round(elapsed_s, 1),
                "longitude": round(lon, 7), "latitude": round(lat, 7),
                "altitude_m": request.release_altitude_m,
            })
        trajectories.append({
            "particle_id": particle_index,
            "altitude_m": request.release_altitude_m,
            "coordinates": points,
            "samples": samples,
        })

    return {
        "id": str(uuid4()),
        "created_at": created.isoformat(),
        "valid_from": created.isoformat(),
        "valid_until": (created + timedelta(minutes=request.duration_minutes)).isoformat(),
        "model": "daedalus.passive_tracer.constant_wind_ensemble.v1",
        "model_class": "simplified_physical_approximation",
        "data_class": "simulated",
        "forcing": {
            "source": forcing_source,
            "wind_speed_mps": base_speed,
            "wind_from_deg": wind_from,
            "reference_height_m": 10,
            "provenance": forcing_provenance,
            "release_to_forcing_distance_km": forcing_provenance.get("release_to_forcing_distance_km"),
        },
        "assumptions": [
            "Wind is spatially uniform and constant over the simulation interval.",
            "Horizontal passive-tracer motion only; vertical motion is not modeled.",
            "No terrain, buildings, surface roughness, Coriolis, chemistry, deposition, or buoyancy.",
            "Ensemble spread is a sensitivity envelope, not a calibrated probability distribution.",
        ],
        "input": request.model_dump(mode="json"),
        "trajectories": trajectories,
    }
