from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx

from ..models import DataClass, GeoPoint, Observation

USGS_FEED = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson"
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
NWS_ALERTS = "https://api.weather.gov/alerts/active"


def parse_dt(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def stable_id(source: str, record_key: str) -> str:
    return hashlib.sha256(f"{source}:{record_key}".encode()).hexdigest()[:32]


class LiveProviders:
    def __init__(self, client: httpx.AsyncClient, latitude: float, longitude: float, location_name: str):
        self.client = client
        self.latitude = latitude
        self.longitude = longitude
        self.location_name = location_name
        self.status: dict[str, dict[str, Any]] = {
            "usgs_earthquakes": {"state": "starting", "last_success": None, "last_error": None},
            "open_meteo": {"state": "starting", "last_success": None, "last_error": None},
            "nws_alerts": {"state": "starting", "last_success": None, "last_error": None},
        }

    def set_location(self, latitude: float, longitude: float, name: str) -> None:
        self.latitude, self.longitude, self.location_name = latitude, longitude, name

    async def _get_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self.client.get(url, params=params)
        response.raise_for_status()
        return response.json()

    def _mark(self, provider: str, error: str | None = None) -> None:
        status = self.status[provider]
        status["checked_at"] = datetime.now(timezone.utc).isoformat()
        if error:
            status["state"] = "degraded"
            status["last_error"] = error[:500]
        else:
            status["state"] = "online"
            status["last_success"] = status["checked_at"]
            status["last_error"] = None

    async def earthquakes(self) -> list[Observation]:
        name = "usgs_earthquakes"
        try:
            payload = await self._get_json(USGS_FEED)
            out: list[Observation] = []
            for feature in payload.get("features", []):
                props = feature.get("properties") or {}
                coords = (feature.get("geometry") or {}).get("coordinates") or []
                if len(coords) < 3 or coords[0] is None or coords[1] is None:
                    continue
                event_id = str(feature.get("id") or props.get("code") or "")
                event_time = datetime.fromtimestamp((props.get("time") or 0) / 1000, tz=timezone.utc)
                magnitude = props.get("mag")
                if magnitude is None:
                    continue
                out.append(Observation(
                    id=stable_id(name, event_id), source=name, source_url=USGS_FEED,
                    source_license="USGS public earthquake feed; attribution required",
                    source_record_id=event_id, kind="hazard.earthquake", data_class=DataClass.OBSERVED,
                    observed_at=event_time,
                    geo=GeoPoint(longitude=float(coords[0]), latitude=float(coords[1])),
                    coordinate_frame="EPSG:4979",
                    values={"magnitude": float(magnitude), "magnitude_type": props.get("magType"), "depth_km": float(coords[2]),
                            "place": props.get("place"), "tsunami_flag": props.get("tsunami", 0),
                            "felt_reports": props.get("felt"), "status": props.get("status")},
                    units={"magnitude": "Mw or source-reported scale", "depth_km": "km"},
                    quality="medium" if props.get("status") == "reviewed" else "low",
                    status=str(props.get("status") or "preliminary"),
                    metadata={"url": props.get("url"), "detail": props.get("detail"), "updated_ms": props.get("updated")},
                ))
            self._mark(name)
            return out
        except Exception as exc:
            self._mark(name, str(exc))
            return []

    async def weather(self) -> list[Observation]:
        name = "open_meteo"
        # Snapshot query context before awaiting the network. Location can change
        # while a request is in flight; metadata must describe the coordinates
        # actually sent to the provider, never the latest mutable selection.
        query_latitude = float(self.latitude)
        query_longitude = float(self.longitude)
        query_location_name = str(self.location_name)
        params = {
            "latitude": query_latitude, "longitude": query_longitude, "timezone": "UTC", "wind_speed_unit": "ms",
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,cloud_cover,pressure_msl,wind_speed_10m,wind_direction_10m,wind_gusts_10m",
            "hourly": "temperature_2m,precipitation_probability,precipitation,wind_speed_10m,wind_direction_10m,wind_gusts_10m",
            "forecast_days": 2,
        }
        try:
            payload = await self._get_json(OPEN_METEO, params)
            current = payload.get("current") or {}
            current_time = parse_dt(current.get("time"))
            # Providers may omit or explicitly return null for resolved coordinates.
            # In that case retain the exact requested query point rather than failing
            # the entire refresh or inventing a new position.
            resolved_latitude_raw = payload.get("latitude")
            resolved_longitude_raw = payload.get("longitude")
            resolved_latitude = float(query_latitude if resolved_latitude_raw is None else resolved_latitude_raw)
            resolved_longitude = float(query_longitude if resolved_longitude_raw is None else resolved_longitude_raw)
            geo = GeoPoint(longitude=resolved_longitude, latitude=resolved_latitude)
            out = [Observation(
                id=stable_id(name, f"current:{resolved_latitude:.4f}:{resolved_longitude:.4f}:{current_time.isoformat()}"),
                source=name, source_url=OPEN_METEO,
                source_license="Open-Meteo API; consult current terms and attribution requirements",
                source_record_id=f"current:{current_time.isoformat()}", kind="weather.current",
                data_class=DataClass.MODEL_ESTIMATE, observed_at=current_time, geo=geo,
                coordinate_frame="EPSG:4326",
                values={
                    "temperature_2m_c": current.get("temperature_2m"),
                    "relative_humidity_2m_pct": current.get("relative_humidity_2m"),
                    "apparent_temperature_c": current.get("apparent_temperature"),
                    "precipitation_mm": current.get("precipitation"),
                    "weather_code": current.get("weather_code"),
                    "cloud_cover_pct": current.get("cloud_cover"),
                    "pressure_msl_hpa": current.get("pressure_msl"),
                    "wind_speed_10m_mps": float(current["wind_speed_10m"]) if current.get("wind_speed_10m") is not None else None,
                    "wind_direction_10m_deg": current.get("wind_direction_10m"),
                    "wind_gusts_10m_mps": float(current["wind_gusts_10m"]) if current.get("wind_gusts_10m") is not None else None,
                },
                units={"temperature_2m_c": "degC", "wind_speed_10m_mps": "m/s",
                       "wind_direction_10m_deg": "degrees_from_north", "precipitation_mm": "mm",
                       "pressure_msl_hpa": "hPa"},
                quality="medium", status="model-derived near-current estimate",
                metadata={"model": payload.get("model"), "timezone": payload.get("timezone"),
                          "requested_location": {"latitude": query_latitude, "longitude": query_longitude},
                          "resolved_model_grid": {"latitude": resolved_latitude, "longitude": resolved_longitude},
                          "location_name": query_location_name, "provider_note": "Not a direct local sensor measurement."},
            )]
            hourly = payload.get("hourly") or {}
            times = hourly.get("time") or []
            arrays = {key: hourly.get(key, []) for key in (
                "temperature_2m", "precipitation_probability", "precipitation", "wind_speed_10m",
                "wind_direction_10m", "wind_gusts_10m")}
            for index, timestamp in enumerate(times):
                valid_at = parse_dt(timestamp)
                if valid_at <= current_time:
                    continue
                vals = {key: (items[index] if index < len(items) else None) for key, items in arrays.items()}
                out.append(Observation(
                    id=stable_id(name, f"forecast:{resolved_latitude:.4f}:{resolved_longitude:.4f}:{valid_at.isoformat()}:{current_time.isoformat()}"),
                    source=name, source_url=OPEN_METEO, source_license="Open-Meteo API; consult current terms",
                    source_record_id=f"forecast:{valid_at.isoformat()}:{current_time.isoformat()}", kind="weather.forecast",
                    data_class=DataClass.FORECAST, observed_at=valid_at,
                    valid_until=valid_at + timedelta(hours=1),
                    geo=geo, coordinate_frame="EPSG:4326",
                    values={
                        "temperature_2m_c": vals["temperature_2m"],
                        "precipitation_probability_pct": vals["precipitation_probability"],
                        "precipitation_mm": vals["precipitation"],
                        "wind_speed_10m_mps": float(vals["wind_speed_10m"]) if vals["wind_speed_10m"] is not None else None,
                        "wind_direction_10m_deg": vals["wind_direction_10m"],
                        "wind_gusts_10m_mps": float(vals["wind_gusts_10m"]) if vals["wind_gusts_10m"] is not None else None,
                    },
                    units={"temperature_2m_c": "degC", "wind_speed_10m_mps": "m/s",
                           "wind_direction_10m_deg": "degrees_from_north", "precipitation_mm": "mm"},
                    quality="medium", status="forecast",
                    metadata={"forecast_reference_time": current_time.isoformat(), "model": payload.get("model"),
                              "requested_location": {"latitude": query_latitude, "longitude": query_longitude},
                              "resolved_model_grid": {"latitude": resolved_latitude, "longitude": resolved_longitude},
                              "location_name": query_location_name},
                ))
            self._mark(name)
            return out
        except Exception as exc:
            self._mark(name, str(exc))
            return []

    async def alerts(self) -> list[Observation]:
        name = "nws_alerts"
        # Keep the queried point and its provenance immutable across the await.
        query_latitude = float(self.latitude)
        query_longitude = float(self.longitude)
        query_location_name = str(self.location_name)
        params = {"point": f"{query_latitude},{query_longitude}", "status": "actual", "message_type": "alert,update"}
        try:
            payload = await self._get_json(NWS_ALERTS, params)
            out: list[Observation] = []
            for feature in payload.get("features", []):
                props = feature.get("properties") or {}
                event_id = str(props.get("id") or feature.get("id") or "")
                geometry = feature.get("geometry")
                coords = None
                if geometry and geometry.get("type") == "Point":
                    raw = geometry.get("coordinates") or []
                    if len(raw) >= 2:
                        coords = GeoPoint(longitude=float(raw[0]), latitude=float(raw[1]))
                issued = parse_dt(props.get("sent") or props.get("effective"))
                effective = parse_dt(props.get("onset") or props.get("effective") or props.get("sent"))
                end = props.get("expires") or props.get("ends")
                out.append(Observation(
                    id=stable_id(name, event_id), source=name, source_url=str(props.get("uri") or NWS_ALERTS),
                    source_license="NOAA National Weather Service API; attribution required",
                    source_record_id=event_id, kind="hazard.weather_alert", data_class=DataClass.OBSERVED,
                    observed_at=issued, valid_from=effective, valid_until=parse_dt(end) if end else None, geo=coords,
                    geometry=geometry if geometry and geometry.get("type") in {"Polygon", "MultiPolygon"} else None,
                    coordinate_frame="EPSG:4326" if coords or geometry else None,
                    values={"event": props.get("event"), "headline": props.get("headline"),
                            "severity": props.get("severity"), "urgency": props.get("urgency"),
                            "certainty": props.get("certainty"), "area_desc": props.get("areaDesc"),
                            "instruction": props.get("instruction")},
                    quality="high", status=str(props.get("status") or "Actual"),
                    metadata={"sent": props.get("sent"), "onset": props.get("onset"), "expires": end,
                              "sender": props.get("senderName"), "geocode": props.get("geocode"),
                              "requested_location": {"latitude": query_latitude, "longitude": query_longitude},
                              "location_name": query_location_name},
                ))
            self._mark(name)
            return out
        except Exception as exc:
            self._mark(name, str(exc))
            return []
