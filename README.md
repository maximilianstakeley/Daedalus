# Daedalus 4D — Phase 5 Live World Simulation Prototype

**Purpose:** a modular research prototype for building a time-aware virtual representation of real environments from public data feeds, local sensors, robotics telemetry, geospatial datasets, and physics-based or statistical model outputs.

This project is a substantial prototype architecture, not a claim that a complete, survey-accurate digital twin or continuously reconstructed 4D world has already been achieved. It separates measurements, provider estimates, forecasts, derived values, and simulation results so that users can inspect what is known, what is estimated, and what is hypothetical.

## Phase 5 iteration notes (0.6.0)

This iteration addresses correctness and synchronization gaps in the previous prototype:

- **Provider request provenance:** Open-Meteo and NWS requests retain the exact coordinates and location label used at request time, even if the operator changes locations while a request is in flight.
- **Live stream reliability:** browser WebSocket heartbeats detect half-open connections, and server broadcasts isolate slow subscribers so one stalled client cannot block other subscribers or the ingestion loop.
- **RGB-D calibration consistency:** the ROS 2 capture bridge rectifies images when the CameraInfo projection intrinsics differ from the raw camera matrix, even if distortion and rectification matrices are otherwise identity.
- **Time-aligned world snapshots:** adds `GET /api/world-state`, which evaluates observations against one explicit UTC simulation time, applies validity intervals and a configurable maximum age, and reports excluded records with reasons (`stale`, `expired`, `not_yet_valid`). This prevents consumers from silently mixing temporally incompatible records.
- **Stale weather forcing protection:** wind-advection runs driven by stored weather now require a timezone-aware observation timestamp, reject forcing more than five minutes in the future, and reject forcing older than the request's `max_forcing_age_minutes` (default 120). The accepted forcing age and threshold are recorded in model provenance. Explicit operator-supplied wind remains available and is labeled separately.
- **Correct filtered result counts:** `/api/observations` now reports the number of records matching its filters rather than the total database size.
- Retains the v0.4.0 selectable playback rates and exponential WebSocket reconnection; the browser cache is capped at 2,500 observations.

The world-state endpoint is an explicit snapshot API; the existing historical archive and playback remain available for retrospective analysis. A stale record is not deleted—it is excluded from the active snapshot and reported in diagnostics.

## Phase 5 iteration notes (0.4.0)

This iteration builds on the previous 4D prototype rather than replacing its data architecture.

- Adds selectable temporal playback rates (1×, 5×, 15× and 60×) so analysts can inspect event evolution at different speeds without changing source timestamps.
- Replaces fixed-interval WebSocket reconnects with capped exponential backoff and jitter, reducing reconnect storms during API restarts or poor network conditions.
- Aligns the frontend and API version at 0.4.0.
- Retains the strict distinction between observed records, model estimates, forecasts, derived values and simulated outputs.
- Retains the separate Cesium globe, deck.gl analytics map, local reconstruction timeline and Gaussian-splat inspection page.

The time cursor changes only the displayed simulation time. It does not rewrite source event times or accelerate any live data provider.

## What “4D” means here

In Daedalus, 4D means **three-dimensional spatial state indexed by time**. It does not mean a fourth spatial dimension. A world state at time `t` is assembled from spatial geometry, geolocated observations, local-coordinate sensor frames, moving entities, forecast fields, simulation state, and the provenance/uncertainty associated with each item.

The prototype currently provides:

- A CesiumJS globe with an accelerated UTC timeline, recent event playback, forecast markers, and time-bounded simulation paths.
- A deck.gl analytical map for time-filtered events and active simulation trajectories.
- Live polling adapters for the USGS earthquake feed, Open-Meteo near-current weather estimates and hourly forecasts, and NOAA/NWS active weather alerts.
- A FastAPI ingest service, WebSocket stream, SQLite event store, optional PostGIS mirror, optional Kafka event publisher, and GeoJSON/timeline endpoints.
- A reproducible passive-tracer wind-advection ensemble using a uniform wind input, with explicit simplifying assumptions.
- A timestamped metric RGB-D reconstruction worker using Open3D TSDF fusion, with a browser 4D mesh timeline for scrubbing independent reconstruction snapshots.
- ROS 2 bridges for odometry/GNSS events and synchronized RGB-D capture with explicit camera calibration and camera-to-base extrinsics.
- Integration scripts for stella_vslam, Nerfstudio Splatfacto, GDAL, GeoServer and PostGIS.
- A separate Gaussian-splat viewer for Nerfstudio `.ply`, `.splat`, and `.ksplat` assets, plus a Three.js PLY mesh viewer for time-indexed Open3D reconstruction outputs.
- A non-destructive importer/patcher for connecting the existing Daedalus Phase 4 browser prototype to the new API.

## System layout

```text
REAL WORLD / RECORDINGS / ROBOTS
  Cameras, metric depth, IMU, GNSS, LiDAR, weather, seismic events, alerts
                |
                v
INGESTION + TIME / FRAME NORMALIZATION
  FastAPI events <--- ROS 2 / browser adapter / sensor agents
                |
                v
EVENT TRANSPORT AND STORAGE
  SQLite (local default) | PostGIS (spatial mirror) | Kafka (optional)
                |
        +-------+---------------------+
        |                             |
        v                             v
RECONSTRUCTION / SLAM              LIVE DATA PROVIDERS
  stella_vslam                    USGS / Open-Meteo / NWS
  Open3D RGB-D TSDF
  Nerfstudio Splatfacto
  GDAL / raster tools
        |                             |
        +---------------+-------------+
                        v
TEMPORAL WORLD STATE + SIMULATION
  provenance, coordinate frames, timestamps, uncertainty,
  observed state, forecast state, derived state, simulated state
                        |
                        v
VISUALIZATION / ANALYSIS
  CesiumJS + deck.gl + Gaussian splat inspection + legacy Three.js/WebXR
```

More detail: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), [`docs/DATA_CONTRACT.md`](docs/DATA_CONTRACT.md), [`docs/VALIDATION_AND_LIMITATIONS.md`](docs/VALIDATION_AND_LIMITATIONS.md), [`docs/ROADMAP.md`](docs/ROADMAP.md), and [`docs/BUILD_STATUS.md`](docs/BUILD_STATUS.md).

## Quick start: browser globe + live public feeds

### Prerequisites

- Python 3.11 or newer is recommended for the API environment.
- Node.js 22.12+ recommended for Vite 8.
- npm.
- A modern browser with WebGL 2.
- Internet access for the public live-data providers and map tiles.

Docker is optional for the local SQLite + API + browser workflow. PostGIS, GeoServer, and Kafka are separate optional services.

### 1. Configure

From the project root:

```bash
cp .env.example .env
```

The initial map centre is a clearly labeled demonstration region. It does not assert your physical location. Change the map location in the UI or edit `.env`.

### 2. Start the API

```bash
python3 -m venv .venv-daedalus
source .venv-daedalus/bin/activate
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt

uvicorn backend.daedalus.app:app --host 127.0.0.1 --port 8767 --reload
```

Health check:

```bash
curl http://127.0.0.1:8767/api/health
```

The default API host is loopback-only. Do not bind it to a public interface without authentication, TLS, network controls, request limits, and production hardening.

### 3. Start the web client

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`. Use the RATE control in the 4D timeline to change replay speed; LIVE returns the cursor to current UTC time.

The frontend copies Cesium runtime assets before Vite starts. The multi-page client includes the Earth view (`/`), Gaussian-splat inspection (`/splats.html`), and time-indexed metric mesh replay (`/reconstruction.html`). By default the globe uses ellipsoid terrain. To enable Cesium World Terrain, copy `frontend/.env.example` to `frontend/.env`, add a Cesium Ion browser token restricted to your development/deployment origins, then restart Vite. The token is visible in browser code and must not be a privileged secret. Cesium's imagery/terrain providers and the deck.gl analytical map use external data services; the current OpenStreetMap tile configuration is for development and evaluation, not unrestricted high-volume production traffic. Use an appropriate tile service and comply with provider terms for a production deployment.

### 4. Check the live data sources

```bash
curl http://127.0.0.1:8767/api/sources
curl -X POST http://127.0.0.1:8767/api/refresh
curl 'http://127.0.0.1:8767/api/observations?limit=10'
# Build a synchronized snapshot for the current UTC time (default maximum age: 15 minutes)
curl 'http://127.0.0.1:8767/api/world-state?max_age_seconds=900'
```

The API polls USGS approximately every 60 seconds and Open-Meteo/NWS approximately every 300 seconds by default. You can change those values in `.env`. `POST /api/refresh` forces an immediate fetch.

**Important source semantics:** USGS earthquake feed entries are source-reported event records; NWS entries are official alert products; Open-Meteo’s near-current weather values are model-derived estimates rather than direct observations from a local physical weather station; its hourly values are forecasts. Daedalus labels these classes separately.

### 5. Run the built-in wind ensemble

Select a region in the UI, wait for the wind estimate, then run the scenario. Or use the API:

```bash
curl -X POST http://127.0.0.1:8767/api/simulations/wind-advection \
  -H 'Content-Type: application/json' \
  -d '{
    "latitude": 27.9506,
    "longitude": -82.4572,
    "particle_count": 48,
    "duration_minutes": 60,
    "timestep_seconds": 60,
    "random_seed": 42,
    "title": "Tampa Bay wind sensitivity run"
  }'
```

This model is a uniform, constant-wind horizontal advection approximation. The API checks that model-derived weather forcing is within 50 km of the requested release point and records that offset. This is a coarse locality guard, not atmospheric interpolation. The model is deliberately **not** represented as a forecast, CFD solution, hazardous-material dispersion model, or emergency-response model. Its uncertainty envelope is a sensitivity ensemble, not a calibrated probability distribution.

## Optional infrastructure

### PostGIS

Start the PostGIS development service:

```bash
docker compose up -d postgis
```

Then set `DAEDALUS_POSTGIS_DSN` in `.env`:

```dotenv
DAEDALUS_POSTGIS_DSN=postgresql://daedalus:change-this-local-dev-password@127.0.0.1:5432/daedalus
```

Restart the API. SQLite remains the local primary event store; PostGIS is an asynchronous spatial mirror in this prototype. Check `/api/health` to confirm the mirror connection.

### GeoServer

```bash
docker compose --profile geospatial up -d
```

Open `http://127.0.0.1:8080/geoserver`. The example starts GeoServer and PostGIS, but does **not** automatically create a workspace, store, or published layer. Configure a PostGIS data store in GeoServer, then publish appropriate tables/views. Change development credentials before use outside localhost.

### Apache Kafka

```bash
docker compose --profile streaming up -d
```

Set in `.env`:

```dotenv
DAEDALUS_KAFKA_BOOTSTRAP_SERVERS=127.0.0.1:9092
DAEDALUS_KAFKA_TOPIC=daedalus.observations
```

Restart the API and check `/api/health`. The Kafka 4.3.2 compose service is a single-node KRaft development broker, not a resilient production cluster.

### PostGIS + GeoServer + Kafka

You can enable all optional infrastructure at once:

```bash
docker compose --profile geospatial --profile streaming up -d
```

## Existing Daedalus Phase 4 integration

The original Phase 4 file is preserved as a separate legacy artifact. Import the current public repository and make a patched copy:

```bash
./tools/import_legacy.sh
python tools/patch_legacy_phase4.py \
  legacy/daedalussimv4.html \
  --output frontend/phase4-integrated.html
```

Open `http://127.0.0.1:5173/phase4-integrated.html` while the API is running.

The patcher adds a bounded telemetry adapter and corrects the original building-collider index construction. It does not edit the copy under `legacy/` or any source in the user's public GitHub repository. The adapter sends coalesced signals to `/api/observations` and separates `sim.*` synthetic signals from camera-derived, model-estimated, and observed sensor values. Local player coordinates remain in `daedalus-local-world`; they are never mislabeled as latitude/longitude.

The earlier Phase 4 social relay uses `8766`; the old sensor bridge uses `8765`; the new event API uses `8767` to avoid a port collision.

## Metric RGB-D reconstruction with Open3D

The worker requires calibrated **metric** RGB-D input and a consistent timestamped camera trajectory. Monocular relative depth from the original Phase 4 preview does not meet this input contract.

Install the optional dependency in a separate environment:

```bash
python3 -m venv .venv-daedalus-recon
source .venv-daedalus-recon/bin/activate
python -m pip install --upgrade pip
python -m pip install -r backend/requirements-reconstruction.txt
```

Prepare the example manifest structure. Replace the intrinsics, frame paths, depth scale, timestamps, and camera poses with actual capture values:

```bash
python backend/daedalus/workers/reconstruct_rgbd.py \
  examples/rgbd_manifest.example.json \
  --output data/reconstructions \
  --window-seconds 10 \
  --voxel-size-m 0.025
```

The sample manifest deliberately points to placeholder frame paths; it will not reconstruct a scene until you add real `frames/color_*.png` and `frames/depth_*.png` assets with correct calibration/poses.

### Capture from ROS 2

Run this in a ROS 2 environment that has `rclpy`, `message_filters`, `cv_bridge`, `sensor_msgs`, `nav_msgs`, OpenCV, and NumPy installed. Create a file with the calibrated `T_base_camera` 4×4 homogeneous transform (camera coordinates transformed into the robot base frame):

```json
[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]
```

The identity is only a placeholder; replace it with a measured extrinsic calibration. Then run:

```bash
python integrations/ros2/rgbd_capture_to_manifest.py \
  --camera-to-base-json examples/camera_to_base.example.json \
  --output data/rgbd_capture \
  --color-topic /camera/color/image_raw \
  --depth-topic /camera/depth/image_rect_raw \
  --camera-info-topic /camera/color/camera_info \
  --odom-topic /odom
```

The node records RGB-D frames, matches them to the nearest odometry pose within a configured time tolerance, and applies `T_world_camera = T_world_base @ T_base_camera`. When image rectification is required, it additionally applies the inverse rectification rotation so the stored camera pose matches the rectified image coordinates. It updates `data/rgbd_capture/manifest.json`. It drops RGB-D pairs without a nearby pose, matching resolution, calibration, or acceptable timestamps. It does not georeference the local map to Earth.

Then run Open3D on the generated manifest:

```bash
python backend/daedalus/workers/reconstruct_rgbd.py \
  data/rgbd_capture/manifest.json \
  --output data/reconstructions
```

Each time bucket produces a separate mesh snapshot and a `timeline.json`. The API exposes this manifest at `/api/reconstructions/timeline` and serves mesh assets under `/reconstructions/`. Open `http://127.0.0.1:5173/reconstruction.html` to scrub and play the snapshots as a time-indexed sequence. This is time-indexed 3D reconstruction, not non-rigid dynamic 4D reconstruction. Motion between windows is represented by separate snapshots; the worker does not infer deforming object trajectories. The viewer keeps the manifest's coordinate frame and metre units visible and does not assume that a local map is georeferenced.

## SLAM / camera trajectory

The integration guide in [`integrations/stella_vslam/README.md`](integrations/stella_vslam/README.md) defines the expected output contract. The user-specified name “OpenVSAM” was interpreted as OpenVSLAM; `stella_vslam` is the more appropriate continuation to evaluate. Build it in a dedicated C++/ROS 2 workspace and pin the project commit and ROS distribution used by an experiment.

Do not convert a ROS `map` or `odom` pose to latitude/longitude by relabeling a coordinate frame. A valid GNSS/INS or surveyed control-point alignment plus a documented transform is required before georeferencing a local reconstruction.

## Nerfstudio / Gaussian splats

Use a separate Nerfstudio-compatible environment, typically Linux with a suitable NVIDIA GPU. Run:

```bash
integrations/nerfstudio/train_splatfacto.sh \
  /path/to/images \
  data/nerfstudio \
  scene_001
```

This preprocesses images, trains Splatfacto, and exports a Gaussian-splat `.ply`. Copy the export into `data/assets/` (or configure another asset hosting path) and open:

```text
http://127.0.0.1:5173/splats.html?url=/assets/scene_001.ply
```

The splat viewer is for inspection of a reconstruction. A static splat is not itself a dynamic 4D model. For time-varying reconstruction, train/produce time-indexed assets and retain their capture intervals and alignment metadata; dynamic Gaussian/NeRF methods are a separate research phase.

## GDAL / GeoServer

Reproject and publish a raster dataset using an explicit source CRS and target CRS:

```bash
integrations/gdal/dem_to_cog.sh \
  source_dem.tif \
  data/assets/dem_projected_cog.tif \
  EPSG:26917
```

This is a template; choose the CRS/resampling method appropriate to the dataset and preserve vertical datum metadata where applicable. Add the COG or its published layer to the geospatial service pipeline. GeoServer must be configured to publish the appropriate data store/layers before Cesium can consume them.

## API quick reference

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | API, provider, Kafka, PostGIS and WebSocket health |
| `GET /api/state` | Current selected region, latest weather estimate, alerts, event summary and source state |
| `POST /api/location` | Change the weather/alert query centre |
| `POST /api/refresh` | Force all live provider refreshes |
| `GET /api/sources` | Provider status and polling intervals |
| `POST /api/observations` | Ingest a validated timestamped event or sensor observation |
| `GET /api/observations` | Query/filter the event history |
| `GET /api/observations.geojson` | Export geographic events as GeoJSON |
| `GET /api/timeline` | Retrieve time-bounded event history |
| `GET /api/world-state` | Build an as-of-time snapshot with freshness and validity filtering; excluded records include diagnostic reasons |
| `POST /api/simulations/wind-advection` | Create a wind-driven ensemble run |
| `GET /api/reconstructions/timeline` | Retrieve time-indexed Open3D mesh snapshots and their served asset URLs |
| `GET /api/simulations` | List saved simulation runs |
| `GET /api/simulations/{id}` | Retrieve a complete simulation run |
| `WS /ws` | Stream newly ingested observations, status and run events |
| `/assets/*` | Serve local 3D/geospatial reconstruction assets |
| `/reconstructions/*` | Serve time-indexed Open3D mesh snapshots |

Example sensor ingest:

```bash
curl -X POST http://127.0.0.1:8767/api/observations \
  -H 'Content-Type: application/json' \
  -d '{
    "source":"field-sensor-01",
    "kind":"environment.temperature",
    "data_class":"observed",
    "observed_at":"2026-10-30T16:00:00Z",
    "coordinate_frame":"EPSG:4326",
    "geo":{"longitude":-82.4572,"latitude":27.9506},
    "values":{"temperature_c":27.1},
    "units":{"temperature_c":"degC"},
    "quality":"medium",
    "metadata":{"instrument":"example probe","calibration_id":"replace-me"}
  }'
```

Replace example timestamp, location, instrument, and calibration details with actual values. Never attach `geo` unless the coordinates are genuinely WGS84 longitude/latitude.

## Tests and validation

Backend tests:

```bash
python -m pip install -r backend/requirements-dev.txt
cd backend
DAEDALUS_DISABLE_POLLING=1 pytest -q
```

Static checks and production build:

```bash
cd frontend
npm install
npm run check
npm run build

cd ..
python -m py_compile backend/daedalus/*.py backend/daedalus/providers/*.py backend/daedalus/workers/*.py integrations/ros2/*.py tools/*.py
```

GitHub Actions runs the backend tests and frontend production build on pushes and pull requests. See [`docs/BUILD_STATUS.md`](docs/BUILD_STATUS.md) for checks executed in the current environment and checks that still require hardware or external services.

The local backend test suite covers schema validation, deduplication, temporal querying, GeoJSON export, deterministic simulation behavior, regional data isolation, live-provider timestamp semantics, and the API ingest/history path. It does not substitute for hardware-in-loop tests, browser GPU tests, ROS 2 topic tests, or a live Docker deployment.

For a single local validation pass (Python tests, Python compilation and JavaScript syntax), run `bash tools/validate.sh` from the repository root. A production browser bundle still requires installed npm dependencies (`npm install && npm run build`).

## Security and production boundary

This repository is configured for localhost development. Before remote or multi-user deployment, add:

- Authentication and per-source authorization for event ingest, location changes, simulation runs and asset access.
- HTTPS/WSS, restrictive CORS, CSRF protection as appropriate, request/body limits, rate limits and abuse controls.
- Secrets management and non-default database/Kafka/GeoServer credentials.
- Kafka replication, retention, schema evolution, dead-letter handling, monitoring and backup procedures.
- PostGIS-primary transaction semantics or an explicit durable outbox, rather than a best-effort mirror.
- A production geospatial tile/terrain provider with contractual usage rights and attribution.
- Observability for clock drift, dropped frames, sensor calibration, provider staleness, queue depth, worker failures and GPU memory.
- Validation datasets and independent ground-truth comparisons before using reconstructed geometry or simulation outputs for operational decisions.

See [`docs/VALIDATION_AND_LIMITATIONS.md`](docs/VALIDATION_AND_LIMITATIONS.md) before interpreting outputs.


## Phase 5.0.6 reliability updates

- Provider requests now snapshot their requested coordinates and location name before network I/O. A location change during an in-flight Open-Meteo or NWS request can no longer cause returned observations to be attributed to the newly selected location.
- The browser WebSocket client now sends application-level keepalive pings every 20 seconds, requires a pong within 45 seconds, and reconnects when the stream becomes half-open. This supplements exponential reconnect backoff.
- The live observation cache is capped at 2,500 records to prevent an indefinitely growing browser-side collection during long sessions.
