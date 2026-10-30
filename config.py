from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), env_file_encoding="utf-8", env_prefix="DAEDALUS_", extra="ignore")

    app_name: str = "Daedalus 4D Simulation API"
    environment: str = "development"
    host: str = "127.0.0.1"
    port: int = 8767
    database_path: str = "./data/daedalus.sqlite3"
    assets_dir: str = "./data/assets"
    reconstructions_dir: str = "./data/reconstructions"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # A configurable initial map centre, not an assertion about the operator's location.
    initial_latitude: float = 27.9506
    initial_longitude: float = -82.4572
    initial_location_name: str = "Tampa Bay demo region"

    usgs_poll_seconds: int = 60
    weather_poll_seconds: int = 300
    alert_poll_seconds: int = 300
    http_timeout_seconds: float = 20.0
    user_agent: str = "Daedalus4DPrototype/0.3 (local research simulator)"

    kafka_bootstrap_servers: str = ""
    kafka_topic: str = "daedalus.observations"
    kafka_client_id: str = "daedalus-api"
    postgis_dsn: str = ""

    max_observation_limit: int = 5000
    max_asset_upload_mb: int = 500

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


settings = Settings()
