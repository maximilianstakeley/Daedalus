from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings
from .live import EventHub, KafkaPublisher, LiveDataService
from .models import LocationUpdate, Observation, WindAdvectionRequest
from .providers.live import LiveProviders
from .postgis import PostGISMirror
from .simulation import simulate_wind_advection
from .store import SQLiteStore


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = SQLiteStore(settings.database_path)
    assets_dir = Path(settings.assets_dir).expanduser()
    reconstructions_dir = Path(settings.reconstructions_dir).expanduser()
    assets_dir.mkdir(parents=True, exist_ok=True)
    reconstructions_dir.mkdir(parents=True, exist_ok=True)
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.http_timeout_seconds),
        headers={"User-Agent": settings.user_agent, "Accept": "application/geo+json, application/json"},
    )
    providers = LiveProviders(client, settings.initial_latitude, settings.initial_longitude, settings.initial_location_name)
    hub = EventHub()
    kafka = KafkaPublisher(settings.kafka_bootstrap_servers, settings.kafka_topic, settings.kafka_client_id)
    await kafka.start()
    postgis = PostGISMirror(settings.postgis_dsn)
    await postgis.connect()
    live = LiveDataService(store, providers, hub, kafka, postgis)
    app.state.store = store
    app.state.http_client = client
    app.state.providers = providers
    app.state.hub = hub
    app.state.kafka = kafka
    app.state.postgis = postgis
    app.state.live = live
    app.state.location = {"latitude": settings.initial_latitude, "longitude": settings.initial_longitude,
                          "name": settings.initial_location_name}
    if __import__("os").getenv("DAEDALUS_DISABLE_POLLING", "0").lower() not in {"1", "true", "yes"}:
        live.start({
            "usgs_earthquakes": settings.usgs_poll_seconds,
            "open_meteo": settings.weather_poll_seconds,
            "nws_alerts": settings.alert_poll_seconds,
        })
    try:
        yield
    finally:
        await live.stop()
        await kafka.close()
        await postgis.close()
        await client.aclose()
        store.close()


app = FastAPI(title=settings.app_name, version="0.6.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


def state(request) -> tuple[SQLiteStore, LiveDataService, EventHub, LiveProviders]:
    return request.app.state.store, request.app.state.live, request.app.state.hub, request.app.state.providers


@app.get("/api/health")
async def health(request: Request):
    store, live, _, providers = state(request)
    return {
        "status": "ok", "service": settings.app_name, "version": app.version,
        "utc": now_utc().isoformat(), "observation_count": store.count_observations(),
        "connected_websocket_clients": len(request.app.state.hub.clients),
        "kafka": {"configured": bool(settings.kafka_bootstrap_servers),
                  "connected": request.app.state.kafka.producer is not None,
                  "error": request.app.state.kafka.error},
        "postgis": {"configured": bool(settings.postgis_dsn),
                    "connected": request.app.state.postgis.connection is not None,
                    "error": request.app.state.postgis.error},
        "providers": providers.status,
    }


@app.get("/api/state")
async def get_state(request: Request):
    store, live, _, providers = state(request)
    location = request.app.state.location
    selected_lat = float(location["latitude"])
    selected_lon = float(location["longitude"])

    def belongs_to_selected_location(item: dict[str, Any]) -> bool:
        requested = (item.get("metadata") or {}).get("requested_location")
        if not isinstance(requested, dict):
            return False
        try:
            return (abs(float(requested["latitude"]) - selected_lat) <= 1e-6 and
                    abs(float(requested["longitude"]) - selected_lon) <= 1e-6)
        except (KeyError, TypeError, ValueError):
            return False

    earthquakes = store.list_observations(limit=12, kind="hazard.earthquake")
    # Current dashboard state must not mix together forecasts/alerts from a
    # previously selected region. The full archive remains available through
    # /api/observations and /api/timeline for cross-region analysis.
    weather_candidates = store.list_observations(limit=500, kind="weather.current")
    weather = next((item for item in weather_candidates if belongs_to_selected_location(item)), None)
    alerts = [item for item in store.list_observations(limit=500, kind="hazard.weather_alert")
              if belongs_to_selected_location(item)][:12]
    forecasts = [item for item in store.list_observations(limit=500, kind="weather.forecast")
                 if belongs_to_selected_location(item)][:24]
    return {
        "utc": now_utc().isoformat(), "location": location,
        "weather": weather,
        "earthquakes": earthquakes, "alerts": alerts, "forecast": forecasts,
        "sources": providers.status, "last_refresh": live.last_refresh,
        "observation_count": store.count_observations(),
    }


@app.post("/api/location")
async def set_location(payload: LocationUpdate, request: Request):
    _, _, hub, providers = state(request)
    providers.set_location(payload.latitude, payload.longitude, payload.name)
    request.app.state.location = payload.model_dump()
    count = await request.app.state.live.refresh_weather()
    count += await request.app.state.live.refresh_alerts()
    message = {"type": "location_changed", "data": request.app.state.location}
    await hub.broadcast(message)
    return {"ok": True, "location": request.app.state.location, "new_observations": count}


@app.post("/api/refresh")
async def refresh(request: Request):
    _, live, _, providers = state(request)
    results = await asyncio.gather(live.refresh_usgs(), live.refresh_weather(), live.refresh_alerts())
    return {"new_observations": sum(results), "by_provider": dict(zip(("usgs_earthquakes", "open_meteo", "nws_alerts"), results)),
            "providers": providers.status}


@app.get("/api/sources")
async def sources(request: Request):
    _, live, _, providers = state(request)
    return {"providers": providers.status, "last_refresh": live.last_refresh,
            "poll_intervals_seconds": {"usgs_earthquakes": settings.usgs_poll_seconds,
                                        "open_meteo": settings.weather_poll_seconds,
                                        "nws_alerts": settings.alert_poll_seconds}}


@app.post("/api/observations", status_code=201)
async def ingest_observation(payload: Observation, request: Request):
    _, live, _, _ = state(request)
    created = await live.ingest(payload)
    return {"ok": True, "created": created, "observation": payload.model_dump(mode="json")}


@app.get("/api/observations")
async def observations(
    request: Request,
    limit: int = Query(default=500, ge=1, le=settings.max_observation_limit),
    since: datetime | None = None,
    until: datetime | None = None,
    kind: str | None = None,
    data_class: str | None = None,
    source: str | None = None,
):
    store, _, _, _ = state(request)
    if (since is not None and since.utcoffset() is None) or (until is not None and until.utcoffset() is None):
        raise HTTPException(422, "since/until timestamps must include a timezone")
    since = since.astimezone(timezone.utc) if since else None
    until = until.astimezone(timezone.utc) if until else None
    if since and until and until < since:
        raise HTTPException(422, "until must be greater than or equal to since")
    return {"items": store.list_observations(limit, since, until, kind, data_class, source),
            "count": store.count_matching_observations(since, until, kind, data_class, source)}


@app.get("/api/observations.geojson")
async def observations_geojson(
    request: Request,
    limit: int = Query(default=2000, ge=1, le=settings.max_observation_limit),
    since: datetime | None = None,
    until: datetime | None = None,
    kind: str | None = None,
):
    store, _, _, _ = state(request)
    if (since is not None and since.utcoffset() is None) or (until is not None and until.utcoffset() is None):
        raise HTTPException(422, "since/until timestamps must include a timezone")
    since = since.astimezone(timezone.utc) if since else None
    until = until.astimezone(timezone.utc) if until else None
    if since and until and until < since:
        raise HTTPException(422, "until must be greater than or equal to since")
    items = store.list_observations(limit, since, until, kind)
    features = []
    for item in items:
        geo = item.get("geo")
        geometry = item.get("geometry")
        frame = item.get("coordinate_frame")
        # RFC 7946 GeoJSON coordinates are WGS84 longitude/latitude. Do not
        # silently export ROS map/odom or other local-frame coordinates as geographic geometry.
        if geometry is not None and frame not in {"EPSG:4326", "EPSG:4979", "WGS84"}:
            continue
        if geometry is None and geo is not None:
            coords = [geo["longitude"], geo["latitude"]]
            if geo.get("altitude_m") is not None:
                coords.append(geo["altitude_m"])
            geometry = {"type": "Point", "coordinates": coords}
        if geometry is None:
            continue
        features.append({"type": "Feature", "id": item["id"], "geometry": geometry,
                         "properties": {"id": item["id"], "kind": item["kind"], "source": item["source"],
                                        "data_class": item["data_class"], "observed_at": item["observed_at"],
                                        "valid_from": item.get("valid_from"), "valid_until": item.get("valid_until"),
                                        "quality": item["quality"], "status": item["status"], "values": item["values"]}})
    return {"type": "FeatureCollection", "features": features}


@app.get("/api/timeline")
async def timeline(
    request: Request,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = Query(default=2000, ge=1, le=settings.max_observation_limit),
):
    store, _, _, _ = state(request)
    end = end or now_utc()
    start = start or (end - timedelta(hours=24))
    if start.utcoffset() is None or end.utcoffset() is None:
        raise HTTPException(422, "start and end must be timezone-aware and start <= end")
    start = start.astimezone(timezone.utc)
    end = end.astimezone(timezone.utc)
    if end < start:
        raise HTTPException(422, "start and end must be timezone-aware and start <= end")
    items = store.list_observations(limit, start, end)
    items.sort(key=lambda item: item["observed_at"])
    return {"start": start.isoformat(), "end": end.isoformat(), "count": len(items), "items": items}


@app.get("/api/world-state")
async def world_state(
    request: Request,
    as_of: datetime | None = None,
    max_age_seconds: int = Query(default=900, ge=1, le=604800),
    limit: int = Query(default=5000, ge=1, le=settings.max_observation_limit),
):
    """Build a time-aligned world snapshot without silently treating stale data as live.

    `as_of` is the simulation clock. Observations outside their validity interval,
    later than that clock, or older than max_age_seconds are excluded from `active`.
    The rejected records remain visible in diagnostics for auditability.
    """
    store, _, _, _ = state(request)
    simulation_time = as_of or now_utc()
    if simulation_time.utcoffset() is None:
        raise HTTPException(422, "as_of must include a timezone")
    simulation_time = simulation_time.astimezone(timezone.utc)
    items = store.list_observations(limit=limit, until=simulation_time)
    active: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for item in items:
        observed_at = datetime.fromisoformat(item["observed_at"].replace("Z", "+00:00")).astimezone(timezone.utc)
        age_seconds = (simulation_time - observed_at).total_seconds()
        valid_from = item.get("valid_from")
        valid_until = item.get("valid_until")
        if age_seconds < 0:
            reason = "timestamp_after_simulation_time"
        elif valid_from and simulation_time < datetime.fromisoformat(valid_from.replace("Z", "+00:00")):
            reason = "not_yet_valid"
        elif valid_until and simulation_time > datetime.fromisoformat(valid_until.replace("Z", "+00:00")):
            reason = "expired"
        elif age_seconds > max_age_seconds:
            reason = "stale"
        else:
            reason = None
        enriched = {**item, "age_seconds": round(age_seconds, 3),
                    "snapshot_status": "active" if reason is None else reason}
        if reason is None:
            active.append(enriched)
        else:
            excluded.append({"id": item["id"], "kind": item["kind"], "source": item["source"],
                             "observed_at": item["observed_at"], "age_seconds": round(age_seconds, 3),
                             "reason": reason})
    active.sort(key=lambda item: (item["observed_at"], item["ingested_at"]))
    return {
        "simulation_time": simulation_time.isoformat(),
        "max_age_seconds": max_age_seconds,
        "active_count": len(active),
        "excluded_count": len(excluded),
        "active": active,
        "excluded": excluded,
        "integrity": "time_filtered_snapshot",
    }


@app.post("/api/simulations/wind-advection")
async def run_wind_advection(payload: WindAdvectionRequest, request: Request):
    store, live, hub, _ = state(request)
    weather = store.latest_by_kind("weather.current")
    try:
        result = simulate_wind_advection(payload, weather)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    store.save_simulation(result["id"], result["model"], payload.title, payload.model_dump(mode="json"), result)
    event = {"type": "simulation_created", "data": {"id": result["id"], "model": result["model"], "title": payload.title}}
    await hub.broadcast(event)
    if live.kafka:
        await live.kafka.publish(event["data"] | {"event_type": "simulation_created", "created_at": result["created_at"]})
    return result


@app.get("/api/reconstructions/timeline")
async def reconstruction_timeline():
    """Return the Open3D worker's time-indexed mesh manifest, if one exists."""
    manifest_path = Path(settings.reconstructions_dir).expanduser() / "timeline.json"
    if not manifest_path.is_file():
        raise HTTPException(404, "No reconstruction timeline exists yet. Run the Open3D worker first.")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(500, f"Reconstruction timeline could not be read: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("snapshots"), list):
        raise HTTPException(500, "Reconstruction timeline must be an object containing a snapshots array")
    root = Path(settings.reconstructions_dir).expanduser().resolve()
    for snapshot in payload["snapshots"]:
        if not isinstance(snapshot, dict):
            continue
        filename = str(snapshot.get("mesh", ""))
        # Accept only direct child files. Resolve symlinks as well so a manifest
        # cannot point the static asset server outside the reconstruction store.
        if not filename or Path(filename).name != filename or "/" in filename or "\\" in filename:
            snapshot["mesh_url"] = None
            snapshot["asset_status"] = "invalid_asset_path"
            continue
        mesh_path = (root / filename).resolve()
        if mesh_path.parent != root:
            snapshot["mesh_url"] = None
            snapshot["asset_status"] = "invalid_asset_path"
            continue
        available = mesh_path.is_file()
        snapshot["mesh_url"] = f"/reconstructions/{filename}" if available else None
        snapshot["asset_status"] = "available" if available else "missing"
    return payload


@app.get("/api/simulations")
async def list_simulations(request: Request, limit: int = Query(default=50, ge=1, le=500)):
    store, _, _, _ = state(request)
    return {"items": store.list_simulations(limit)}


@app.get("/api/simulations/{run_id}")
async def get_simulation(run_id: str, request: Request):
    store, _, _, _ = state(request)
    result = store.get_simulation(run_id)
    if result is None:
        raise HTTPException(404, "Simulation run not found")
    return result


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.app.state.hub.connect(websocket)
    try:
        while True:
            # Client messages are limited to a low-cost ping/keepalive contract.
            message = await websocket.receive_text()
            if message == "ping":
                await websocket.send_json({"type": "pong", "utc": now_utc().isoformat()})
    except WebSocketDisconnect:
        await websocket.app.state.hub.disconnect(websocket)
    except Exception:
        await websocket.app.state.hub.disconnect(websocket)


assets_path = Path(settings.assets_dir).expanduser()
reconstructions_path = Path(settings.reconstructions_dir).expanduser()
assets_path.mkdir(parents=True, exist_ok=True)
reconstructions_path.mkdir(parents=True, exist_ok=True)
app.mount("/assets", StaticFiles(directory=str(assets_path)), name="assets")
app.mount("/reconstructions", StaticFiles(directory=str(reconstructions_path)), name="reconstructions")
