from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import json
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DataClass(str, Enum):
    OBSERVED = "observed"
    MODEL_ESTIMATE = "model_estimate"
    FORECAST = "forecast"
    DERIVED = "derived"
    SIMULATED = "simulated"


class GeoPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)
    altitude_m: float | None = Field(default=None, allow_inf_nan=False)


class Observation(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str = Field(default_factory=lambda: str(uuid4()))
    source: str = Field(min_length=1, max_length=120)
    source_url: str | None = None
    source_license: str | None = None
    source_record_id: str | None = None
    kind: str = Field(min_length=1, max_length=100)
    data_class: DataClass
    observed_at: datetime
    ingested_at: datetime = Field(default_factory=utc_now)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    geo: GeoPoint | None = None
    geometry: dict[str, Any] | None = None  # Optional GeoJSON geometry, e.g. NWS alert polygons.
    coordinate_frame: str | None = None
    values: dict[str, Any] = Field(default_factory=dict)
    units: dict[str, str] = Field(default_factory=dict)
    uncertainty: dict[str, Any] = Field(default_factory=dict)
    quality: Literal["unknown", "low", "medium", "high"] = "unknown"
    status: str = "available"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("observed_at", "ingested_at", "valid_from", "valid_until")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("timestamps must include a timezone")
            # Normalize all persisted event times. SQLite stores ISO text, whose
            # lexical ordering is only chronologically reliable after UTC normalization.
            return value.astimezone(timezone.utc)
        return value

    @field_validator("values", "units", "uncertainty", "metadata", "geometry")
    @classmethod
    def require_json_finite_values(cls, value):
        if value is not None:
            try:
                json.dumps(value, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise ValueError("observation payload fields must be JSON-serializable and finite") from exc
        return value

    @model_validator(mode="after")
    def validate_interval(self) -> "Observation":
        if self.valid_until and self.valid_until < self.observed_at:
            raise ValueError("valid_until cannot precede observed_at")
        if self.valid_from and self.valid_until and self.valid_until < self.valid_from:
            raise ValueError("valid_until cannot precede valid_from")
        wgs84_frames = {"EPSG:4326", "EPSG:4979", "WGS84"}
        if self.geo is not None:
            if self.coordinate_frame is None:
                # GeoPoint is explicitly longitude/latitude; make its CRS explicit
                # on persistence rather than allowing an ambiguous frame.
                self.coordinate_frame = "EPSG:4326"
            elif self.coordinate_frame not in wgs84_frames:
                raise ValueError("geo longitude/latitude fields require a WGS84 coordinate_frame")
        if self.geometry is not None and not self.coordinate_frame:
            raise ValueError("geometry requires an explicit coordinate_frame")
        return self


class LocationUpdate(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    name: str = Field(default="Selected simulation region", max_length=160)


class WindAdvectionRequest(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    release_altitude_m: float = Field(default=10.0, ge=0, le=10000)
    duration_minutes: int = Field(default=60, ge=1, le=360)
    timestep_seconds: int = Field(default=60, ge=10, le=600)
    particle_count: int = Field(default=32, ge=1, le=256)
    wind_speed_mps: float | None = Field(default=None, ge=0, le=100)
    wind_from_deg: float | None = Field(default=None, ge=0, le=360)
    speed_sigma_fraction: float = Field(default=0.15, ge=0, le=1)
    direction_sigma_deg: float = Field(default=8.0, ge=0, le=90)
    max_forcing_age_minutes: int = Field(default=120, ge=1, le=1440)
    random_seed: int = 42
    title: str = Field(default="Wind advection sensitivity run", max_length=160)

    @model_validator(mode="after")
    def require_wind_pair(self) -> "WindAdvectionRequest":
        if (self.wind_speed_mps is None) != (self.wind_from_deg is None):
            raise ValueError("wind_speed_mps and wind_from_deg must either both be provided or both omitted")
        return self
