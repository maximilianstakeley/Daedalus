from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx

from daedalus.providers.live import LiveProviders


def test_open_meteo_uses_requested_location_when_resolved_coordinates_are_null_and_revises_forecasts():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        current_time = "2026-10-30T12:00" if calls == 1 else "2026-10-30T13:00"
        payload = {
            "latitude": None,
            "longitude": None,
            "timezone": "GMT",
            "model": "test-model",
            "current": {
                "time": current_time,
                "temperature_2m": 25.0,
                "wind_speed_10m": 4.0,
                "wind_direction_10m": 270,
            },
            "hourly": {
                "time": ["2026-10-30T13:00", "2026-10-30T14:00", "2026-10-30T15:00"],
                "temperature_2m": [25.0, 24.0, 23.0],
                "precipitation_probability": [0, 10, 20],
                "precipitation": [0.0, 0.1, 0.2],
                "wind_speed_10m": [4.0, 5.0, 6.0],
                "wind_direction_10m": [270, 275, 280],
                "wind_gusts_10m": [6.0, 7.0, 8.0],
            },
        }
        return httpx.Response(200, json=payload)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            providers = LiveProviders(client, latitude=27.95, longitude=-82.45, location_name="test region")
            first = await providers.weather()
            second = await providers.weather()
            return providers, first, second

    providers, first, second = asyncio.run(exercise())

    assert providers.status["open_meteo"]["state"] == "online"
    assert first[0].geo.latitude == 27.95
    assert first[0].geo.longitude == -82.45
    assert first[0].metadata["requested_location"] == {"latitude": 27.95, "longitude": -82.45}

    first_forecast = next(item for item in first if item.kind == "weather.forecast" and item.observed_at.hour == 14)
    second_forecast = next(item for item in second if item.kind == "weather.forecast" and item.observed_at.hour == 14)
    assert first_forecast.id != second_forecast.id
    assert first_forecast.valid_until == datetime(2026, 10, 30, 15, 0, tzinfo=timezone.utc)
    assert second_forecast.metadata["forecast_reference_time"].startswith("2026-10-30T13:00:00")


def test_nws_alert_preserves_issued_effective_and_expiry_times_and_query_region():
    requested_params = {}

    def handler(request: httpx.Request) -> httpx.Response:
        requested_params.update(dict(request.url.params))
        return httpx.Response(200, json={
            "features": [{
                "id": "urn:oid:2.49.0.1.840.0.test-alert",
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-82.6, 27.8], [-82.3, 27.8], [-82.3, 28.0], [-82.6, 28.0], [-82.6, 27.8]]],
                },
                "properties": {
                    "id": "urn:oid:2.49.0.1.840.0.test-alert",
                    "sent": "2026-10-30T15:00:00-04:00",
                    "effective": "2026-10-30T15:00:00-04:00",
                    "onset": "2026-10-30T16:00:00-04:00",
                    "expires": "2026-10-30T20:00:00-04:00",
                    "event": "Severe Thunderstorm Warning",
                    "headline": "Test alert headline",
                    "severity": "Severe",
                    "urgency": "Immediate",
                    "certainty": "Observed",
                    "areaDesc": "Test county",
                    "status": "Actual",
                    "senderName": "NWS Test Office",
                    "uri": "https://api.weather.gov/alerts/test-alert",
                },
            }]
        })

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            providers = LiveProviders(client, latitude=27.95, longitude=-82.45, location_name="test region")
            items = await providers.alerts()
            return providers, items

    providers, items = asyncio.run(exercise())

    assert requested_params["point"] == "27.95,-82.45"
    assert providers.status["nws_alerts"]["state"] == "online"
    assert len(items) == 1
    item = items[0]
    assert item.observed_at.isoformat() == "2026-10-30T19:00:00+00:00"
    assert item.valid_from.isoformat() == "2026-10-30T20:00:00+00:00"
    assert item.valid_until.isoformat() == "2026-10-31T00:00:00+00:00"
    assert item.coordinate_frame == "EPSG:4326"
    assert item.geometry["type"] == "Polygon"
    assert item.metadata["requested_location"] == {"latitude": 27.95, "longitude": -82.45}


def test_open_meteo_inflight_request_keeps_original_location_provenance():
    async def exercise():
        started = asyncio.Event()
        release = asyncio.Event()

        class Response:
            def raise_for_status(self):
                return None
            def json(self):
                return {
                    "latitude": None, "longitude": None, "timezone": "GMT", "model": "test",
                    "current": {"time": "2026-10-30T12:00", "temperature_2m": 24.0,
                                "wind_speed_10m": 3.0, "wind_direction_10m": 180},
                    "hourly": {"time": [], "temperature_2m": [], "precipitation_probability": [],
                               "precipitation": [], "wind_speed_10m": [], "wind_direction_10m": [],
                               "wind_gusts_10m": []},
                }

        class Client:
            async def get(self, url, params=None):
                assert params["latitude"] == 27.95
                assert params["longitude"] == -82.45
                started.set()
                await release.wait()
                return Response()

        providers = LiveProviders(Client(), 27.95, -82.45, "original region")
        task = asyncio.create_task(providers.weather())
        await started.wait()
        providers.set_location(40.7, -74.0, "new region")
        release.set()
        observations = await task
        return observations

    observations = asyncio.run(exercise())
    assert observations[0].geo.latitude == 27.95
    assert observations[0].geo.longitude == -82.45
    assert observations[0].metadata["requested_location"] == {"latitude": 27.95, "longitude": -82.45}
    assert observations[0].metadata["location_name"] == "original region"


def test_event_hub_isolates_slow_websocket_subscribers():
    from daedalus.live import EventHub

    class Socket:
        def __init__(self, delay=0):
            self.delay = delay
            self.messages = []
        async def send_json(self, message):
            if self.delay:
                await asyncio.sleep(self.delay)
            self.messages.append(message)

    async def exercise():
        hub = EventHub()
        slow = Socket(delay=3)
        fast = Socket()
        hub.clients.update({slow, fast})
        await hub.broadcast({"type": "test"})
        return hub, slow, fast

    hub, slow, fast = asyncio.run(exercise())
    assert fast.messages == [{"type": "test"}]
    assert slow.messages == []
    assert slow not in hub.clients
    assert fast in hub.clients
