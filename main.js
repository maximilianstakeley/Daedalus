import * as Cesium from 'cesium';
import 'cesium/Build/Cesium/Widgets/widgets.css';
import './style.css';
import {Deck, MapView} from '@deck.gl/core';
import {ScatterplotLayer, PathLayer, BitmapLayer} from '@deck.gl/layers';
import {TileLayer} from '@deck.gl/geo-layers';

const $ = (id) => document.getElementById(id);
const API = '';

// Cesium Ion tokens are browser-visible credentials: restrict the token to the
// intended development/deployment origins. Without a token, use ellipsoid terrain.
const cesiumIonToken = (import.meta.env.VITE_CESIUM_ION_TOKEN || '').trim();
if (cesiumIonToken) Cesium.Ion.defaultAccessToken = cesiumIonToken;

const state = {
  observations: [],
  scenarios: [],
  trajectories: [],
  location: {latitude: 27.9506, longitude: -82.4572, name: 'Tampa Bay demo region'},
  weather: null,
  sourceStatus: {},
  selectedTime: Date.now(),
  rangeStart: Date.now() - 24 * 3600_000,
  rangeEnd: Date.now() + 48 * 3600_000,
  playing: true,
  playbackRate: 5,
  lastTick: performance.now(),
  visible: {earthquakes: true, alerts: true, forecast: true, simulations: true},
};

const viewer = new Cesium.Viewer('cesium-container', {
  animation: false,
  timeline: false,
  geocoder: false,
  baseLayerPicker: false,
  sceneModePicker: false,
  navigationHelpButton: false,
  homeButton: false,
  fullscreenButton: false,
  infoBox: true,
  selectionIndicator: true,
  scene3DOnly: true,
  terrain: cesiumIonToken
    ? Cesium.Terrain.fromWorldTerrain({requestWaterMask: true, requestVertexNormals: true})
    : undefined,
  baseLayer: false,
  shouldAnimate: false,
});
viewer.imageryLayers.addImageryProvider(new Cesium.UrlTemplateImageryProvider({
  url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
  credit: '© OpenStreetMap contributors',
  maximumLevel: 19,
}));
viewer.scene.globe.enableLighting = true;
$('terrain-mode').textContent = cesiumIonToken ? 'CESIUM WORLD TERRAIN' : 'ELLIPSOID TERRAIN · ION TOKEN NOT SET';
viewer.scene.globe.depthTestAgainstTerrain = false;
viewer.scene.skyAtmosphere.show = true;
viewer.scene.fog.enabled = true;
viewer.camera.setView({
  destination: Cesium.Cartesian3.fromDegrees(state.location.longitude, state.location.latitude, 2_500_000),
  orientation: {heading: 0, pitch: Cesium.Math.toRadians(-65), roll: 0},
});

const eventDataSource = new Cesium.CustomDataSource('live-observations');
const forecastDataSource = new Cesium.CustomDataSource('weather-forecast');
const simulationDataSource = new Cesium.CustomDataSource('simulation-runs');
viewer.dataSources.add(eventDataSource);
viewer.dataSources.add(forecastDataSource);
viewer.dataSources.add(simulationDataSource);

let deckViewState = {longitude: state.location.longitude, latitude: state.location.latitude, zoom: 3, pitch: 0, bearing: 0};
let deck;
deck = new Deck({
  parent: $('deck-container'),
  views: new MapView({id: 'analysis-map', repeat: false}),
  initialViewState: deckViewState,
  controller: true,
  onViewStateChange: ({viewState}) => {
    // Keep the controller-driven view state current once the deck becomes controlled.
    deckViewState = viewState;
    deck.setProps({viewState: deckViewState});
  },
  getTooltip: ({object}) => object?.tooltip || object?.properties?.label || null,
  layers: [],
});

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>\"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '\"': '&quot;', "'": '&#39;'
  })[ch]);
}
function safeText(value, fallback = '—') {
  return escapeHtml(value === null || value === undefined || value === '' ? fallback : value);
}
function fmt(value, digits = 1) {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(digits) : '—';
}
function dateString(value) {
  if (!value) return 'time unavailable';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'time unavailable' : date.toLocaleString([], {timeZone: 'UTC', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false}) + ' UTC';
}
function showStatus(element, message, error = false) {
  element.textContent = message;
  element.style.color = error ? '#ff8990' : '';
}

async function api(path, options = {}) {
  const response = await fetch(`${API}${path}`, {
    ...options,
    headers: {'Content-Type': 'application/json', ...(options.headers || {})},
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`${response.status} ${response.statusText}: ${body.slice(0, 240)}`);
  }
  return response.json();
}

function updateWeather(weather) {
  state.weather = weather || null;
  const values = weather?.values || {};
  $('temperature').textContent = values.temperature_2m_c == null ? '--°' : `${fmt(values.temperature_2m_c, 0)}°C`;
  $('weather-place').textContent = weather?.metadata?.location_name || state.location.name;
  $('wind').textContent = values.wind_speed_10m_mps == null ? '—' : `${fmt(values.wind_speed_10m_mps)} m/s`;
  $('wind-dir').textContent = values.wind_direction_10m_deg == null ? '—' : `${fmt(values.wind_direction_10m_deg, 0)}°`;
  $('humidity').textContent = values.relative_humidity_2m_pct == null ? '—' : `${fmt(values.relative_humidity_2m_pct, 0)}%`;
  $('pressure').textContent = values.pressure_msl_hpa == null ? '—' : `${fmt(values.pressure_msl_hpa, 0)} hPa`;
  $('precip').textContent = values.precipitation_mm == null ? '—' : `${fmt(values.precipitation_mm)} mm`;
  $('cloud').textContent = values.cloud_cover_pct == null ? '—' : `${fmt(values.cloud_cover_pct, 0)}%`;
  $('weather-time').textContent = weather ? `Model estimate valid ${dateString(weather.observed_at)} · ${weather.status}` : 'No weather estimate received';
}

function renderSourceHealth(sources = {}) {
  state.sourceStatus = sources;
  const rows = Object.entries(sources);
  const online = rows.filter(([, item]) => item.state === 'online').length;
  $('source-count').textContent = `${online}/${Math.max(rows.length, 3)}`;
  $('source-list').innerHTML = rows.map(([name, item]) => `
    <div class="source-row"><i class="source-dot ${item.state === 'online' ? 'online' : ''}"></i>
      <span>${escapeHtml(name.replaceAll('_', ' '))}</span><span>${escapeHtml(item.state || 'unknown')}</span></div>`).join('') || '<div class="empty">Provider status unavailable.</div>';
}

function renderLists() {
  const quakes = state.observations.filter(item => item.kind === 'hazard.earthquake')
    .sort((a, b) => new Date(b.observed_at) - new Date(a.observed_at)).slice(0, 8);
  $('quake-list').innerHTML = quakes.length ? quakes.map(item => `
    <div class="event-row quake"><strong>M${fmt(item.values?.magnitude, 1)} · ${safeText(item.values?.place, 'Epicenter')}</strong>
      <small>${dateString(item.observed_at)} · depth ${fmt(item.values?.depth_km, 1)} km · ${safeText(item.status)}</small></div>`).join('') : '<div class="empty">No earthquake records in the current feed window.</div>';

  const alerts = state.observations.filter(item => item.kind === 'hazard.weather_alert' && (!item.valid_until || new Date(item.valid_until).getTime() > Date.now()))
    .sort((a, b) => new Date(b.observed_at) - new Date(a.observed_at)).slice(0, 8);
  $('alert-list').innerHTML = alerts.length ? alerts.map(item => `
    <div class="event-row alert"><strong>${safeText(item.values?.event, 'Weather alert')}</strong>
      <small>${safeText(item.values?.severity)} · ${safeText(item.values?.area_desc, 'Area not specified')} · expires ${dateString(item.valid_until)}</small></div>`).join('') : '<div class="empty">No active NWS alerts returned for this region.</div>';
  $('obs-count').textContent = state.observations.length.toLocaleString();
}


const deckBaseLayer = new TileLayer({
  id: 'osm-basemap', data: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png', minZoom: 0, maxZoom: 19, tileSize: 256,
  getTileData: async ({index, signal}) => {
    const response = await fetch(`https://tile.openstreetmap.org/${index.z}/${index.x}/${index.y}.png`, {signal});
    if (!response.ok) throw new Error(`Tile request failed: ${response.status}`);
    if (signal.aborted) return null;
    return createImageBitmap(await response.blob());
  },
  renderSubLayers: props => {
    const bounds = props.tile.boundingBox;
    return new BitmapLayer(props, {
      data: null,
      image: props.data,
      bounds: [bounds[0][0], bounds[0][1], bounds[1][0], bounds[1][1]],
    });
  },
});

let deckTimeBucket = -1;
function updateDeck(force = false) {
  const cursor = state.selectedTime;
  const bucket = Math.floor(cursor / 5000);
  if (!force && bucket === deckTimeBucket) return;
  deckTimeBucket = bucket;
  const windowStart = cursor - 24 * 3600_000;
  const visible = state.observations.filter(item => {
    const time = new Date(item.observed_at).getTime();
    return time >= windowStart && time <= cursor && item.geo && item.kind !== 'weather.forecast';
  });
  const points = visible.map(item => ({
    position: [item.geo.longitude, item.geo.latitude, item.kind === 'hazard.earthquake' ? 0 : (item.geo.altitude_m || 0)],
    kind: item.kind, dataClass: item.data_class, magnitude: item.values?.magnitude,
    tooltip: `${item.kind} · ${item.source} · ${item.data_class} · ${dateString(item.observed_at)}`, 
  }));
  const paths = state.trajectories.flatMap(trajectory => {
    const run = state.scenarios.find(item => item.id === trajectory.run_id);
    if (!run || !state.visible.simulations) return [];
    const start = new Date(run.created_at).getTime();
    const lastElapsed = trajectory.coordinates?.at(-1)?.[2] ?? 0;
    const end = run.valid_until ? new Date(run.valid_until).getTime() : start + lastElapsed * 1000;
    if (cursor < start || cursor > end) return [];
    const travelled = (trajectory.coordinates || []).filter(point => start + Number(point[2] || 0) * 1000 <= cursor);
    if (travelled.length < 2) return [];
    return [{
      path: travelled.map(point => [point[0], point[1], Number(trajectory.altitude_m) || 0]),
      tooltip: `Particle ${trajectory.particle_id} · ${run.title || run.model_name}`,
    }];
  });
  deck.setProps({layers: [deckBaseLayer,
    new ScatterplotLayer({
      id: 'time-filtered-observations', data: points, pickable: true,
      getPosition: d => d.position,
      getRadius: d => d.kind === 'hazard.earthquake' ? Math.max(5000, (Number(d.magnitude) || 1) * 12000) : 5000,
      radiusUnits: 'meters', radiusMinPixels: 3, radiusMaxPixels: 22,
      getFillColor: d => d.kind === 'hazard.earthquake' ? [255, 91, 104, 205] :
        d.dataClass === 'forecast' ? [105, 186, 255, 190] :
        d.dataClass === 'model_estimate' ? [186, 139, 255, 190] :
        d.dataClass === 'derived' ? [255, 185, 92, 190] :
        d.dataClass === 'simulated' ? [91, 238, 193, 190] : [91, 193, 255, 190],
      getLineColor: [255, 255, 255, 175], lineWidthMinPixels: 1,
    }),
    new PathLayer({id: 'scenario-particle-tracks', data: paths, pickable: true,
      getPath: d => d.path, getColor: [87, 238, 193, 200], getWidth: 2, widthUnits: 'pixels'}),
  ]});
  $('analytics-count').textContent = `${points.length} observations · ${paths.length} active trajectories`;
}

function clearCesiumSources() {
  eventDataSource.entities.removeAll();
  forecastDataSource.entities.removeAll();
  simulationDataSource.entities.removeAll();
}

let eventEntityRecords = [];
let forecastEntityRecords = [];
let scenarioEntityRecords = [];

function polygonPositions(ring) {
  const flat = [];
  for (const coordinate of ring || []) {
    if (Array.isArray(coordinate) && coordinate.length >= 2) flat.push(Number(coordinate[0]), Number(coordinate[1]));
  }
  return flat.length >= 6 ? Cesium.Cartesian3.fromDegreesArray(flat) : [];
}

function rebuildCesiumSources() {
  clearCesiumSources();
  eventEntityRecords = [];
  forecastEntityRecords = [];
  scenarioEntityRecords = [];

  for (const item of state.observations) {
    if (item.kind === 'weather.forecast' && item.geo) {
      const entity = forecastDataSource.entities.add({
        name: `Forecast · ${dateString(item.observed_at)}`,
        position: Cesium.Cartesian3.fromDegrees(item.geo.longitude, item.geo.latitude, 0),
        point: {pixelSize: 12, color: Cesium.Color.fromCssColorString('#69baff'), outlineColor: Cesium.Color.WHITE, outlineWidth: 1, heightReference: Cesium.HeightReference.CLAMP_TO_GROUND},
        label: {text: `Forecast ${fmt(item.values?.temperature_2m_c, 0)}°C`, font: '11px monospace', fillColor: Cesium.Color.WHITE,
          outlineColor: Cesium.Color.BLACK, outlineWidth: 3, style: Cesium.LabelStyle.FILL_AND_OUTLINE,
          pixelOffset: new Cesium.Cartesian2(0, -18)},
        description: `<pre>${escapeHtml(JSON.stringify(item.values, null, 2))}</pre>`,
        show: false,
      });
      forecastEntityRecords.push({entity, time: new Date(item.observed_at).getTime()});
      continue;
    }

    if (item.kind === 'hazard.earthquake' && item.geo) {
      const magnitude = Number(item.values?.magnitude || 0);
      const entity = eventDataSource.entities.add({
        id: item.id,
        name: `M${magnitude.toFixed(1)} ${item.values?.place || 'Earthquake'}`,
        position: Cesium.Cartesian3.fromDegrees(item.geo.longitude, item.geo.latitude, Math.max(0, item.geo.altitude_m || 0)),
        point: {pixelSize: Math.min(24, 7 + magnitude * 2), color: Cesium.Color.fromCssColorString('#ff6270'),
          outlineColor: Cesium.Color.WHITE, outlineWidth: 1, heightReference: Cesium.HeightReference.CLAMP_TO_GROUND},
        description: `<b>USGS earthquake</b><br>Time: ${escapeHtml(item.observed_at)}<br>Quality: ${escapeHtml(item.quality)}<br><pre>${escapeHtml(JSON.stringify(item.values, null, 2))}</pre>`,
        show: false,
      });
      eventEntityRecords.push({entity, time: new Date(item.observed_at).getTime(), kind: item.kind});
    }

    if (item.kind === 'hazard.weather_alert') {
      const entities = [];
      if (item.geo) {
        entities.push(eventDataSource.entities.add({
          name: item.values?.event || 'NWS weather alert',
          position: Cesium.Cartesian3.fromDegrees(item.geo.longitude, item.geo.latitude),
          point: {pixelSize: 11, color: Cesium.Color.fromCssColorString('#eac56c'), outlineColor: Cesium.Color.WHITE, outlineWidth: 1},
          description: `<b>${safeText(item.values?.event, 'Weather alert')}</b><br>${safeText(item.values?.headline, '')}<br>${safeText(item.values?.area_desc, '')}`,
          show: false,
        }));
      }
      const geometry = item.geometry;
      const polygons = geometry?.type === 'Polygon' ? [geometry.coordinates] : geometry?.type === 'MultiPolygon' ? geometry.coordinates : [];
      for (const polygon of polygons) {
        const outer = polygonPositions(polygon[0]);
        if (outer.length < 3) continue;
        entities.push(eventDataSource.entities.add({
          name: item.values?.event || 'NWS alert area',
          polygon: {hierarchy: new Cesium.PolygonHierarchy(outer), material: Cesium.Color.fromCssColorString('#e6b84d').withAlpha(0.18),
            outline: true, outlineColor: Cesium.Color.fromCssColorString('#e6b84d'), height: 0},
          description: `<b>${safeText(item.values?.event, 'Weather alert')}</b><br>${safeText(item.values?.headline, '')}<br>${safeText(item.values?.instruction, '')}`,
          show: false,
        }));
      }
      for (const entity of entities) eventEntityRecords.push({
        entity,
        time: new Date(item.observed_at).getTime(),
        validFrom: item.valid_from ? new Date(item.valid_from).getTime() : null,
        validUntil: item.valid_until ? new Date(item.valid_until).getTime() : Infinity,
        kind: item.kind,
      });
    }
  }

  for (const trajectory of state.trajectories) {
    const run = state.scenarios.find(item => item.id === trajectory.run_id);
    const runStart = new Date(run?.created_at || Date.now());
    const runEnd = new Date(run?.valid_until || (runStart.getTime() + 6 * 3600_000));
    const sampled = new Cesium.SampledPositionProperty();
    const altitudeM = Number.isFinite(Number(trajectory.altitude_m)) ? Number(trajectory.altitude_m) : 0;
    for (const [lon, lat, elapsed] of trajectory.coordinates) {
      const when = Cesium.JulianDate.addSeconds(Cesium.JulianDate.fromDate(runStart), elapsed, new Cesium.JulianDate());
      sampled.addSample(when, Cesium.Cartesian3.fromDegrees(lon, lat, altitudeM));
    }
    const entity = simulationDataSource.entities.add({
      name: `Scenario particle ${trajectory.particle_id}`,
      position: sampled,
      point: {pixelSize: 4, color: Cesium.Color.fromCssColorString('#61e9bf')},
      path: {resolution: 10, material: new Cesium.PolylineGlowMaterialProperty({glowPower: 0.12, color: Cesium.Color.fromCssColorString('#62edc4')}),
        width: 2, leadTime: 0, trailTime: 1e9},
      show: false,
    });
    scenarioEntityRecords.push({entity, start: runStart.getTime(), end: runEnd.getTime()});
  }
  updateTemporalVisibility();
  updateDeck(true);
}

function updateTemporalVisibility() {
  const cursor = state.selectedTime;
  const pastStart = cursor - 24 * 3600_000;
  for (const record of eventEntityRecords) {
    record.entity.show = record.time >= pastStart && record.time <= cursor &&
      (!record.validFrom || record.validFrom <= cursor) &&
      (!record.validUntil || record.validUntil >= cursor) &&
      (record.kind === 'hazard.earthquake' ? state.visible.earthquakes : state.visible.alerts);
  }
  let nearestForecast = null;
  for (const record of forecastEntityRecords) {
    const delta = Math.abs(record.time - cursor);
    if (delta < 90 * 60_000 && (!nearestForecast || delta < nearestForecast.delta)) nearestForecast = {...record, delta};
    record.entity.show = false;
  }
  if (nearestForecast && state.visible.forecast) nearestForecast.entity.show = true;
  for (const record of scenarioEntityRecords) {
    record.entity.show = state.visible.simulations && cursor >= record.start && cursor <= record.end;
  }
  viewer.clock.currentTime = Cesium.JulianDate.fromDate(new Date(cursor));
  viewer.clock.shouldAnimate = false;
  const cursorDate = new Date(cursor);
  $('timeline-label').textContent = Math.abs(cursor - Date.now()) < 60_000 ? 'LIVE · NOW' : `REPLAY · ${cursorDate.toISOString().replace('T', ' ').slice(0, 19)} UTC`;
}

function updateTimelineBounds() {
  const input = $('timeline');
  input.min = '0'; input.max = '10000';
  input.value = String(Math.round((state.selectedTime - state.rangeStart) / (state.rangeEnd - state.rangeStart) * 10000));
  $('timeline-start').textContent = dateString(state.rangeStart).replace(' UTC', '');
  $('timeline-end').textContent = dateString(state.rangeEnd).replace(' UTC', '');
}
function setTimelineFromInput() {
  const ratio = Number($('timeline').value) / 10000;
  state.selectedTime = state.rangeStart + ratio * (state.rangeEnd - state.rangeStart);
  updateTemporalVisibility(); updateDeck(true);
}

function renderScenarios() {
  $('scenario-list').innerHTML = state.scenarios.length ? state.scenarios.slice(0, 6).map(run => `
    <div class="event-row"><strong>${safeText(run.title || run.model_name)}</strong><small>${safeText(run.model_name)} · ${dateString(run.created_at)}</small></div>`).join('') : '<div class="empty">No scenario runs in this session.</div>';
}

async function refreshState() {
  try {
    const [snapshot, observationResponse, simulationResponse] = await Promise.all([
      api('/api/state'), api('/api/observations?limit=2500'), api('/api/simulations?limit=50'),
    ]);
    state.location = snapshot.location || state.location;
    state.observations = observationResponse.items || [];
    state.scenarios = simulationResponse.items || [];
    state.weather = snapshot.weather;
    updateWeather(snapshot.weather);
    renderSourceHealth(snapshot.sources || {});
    renderLists();
    state.trajectories = [];
    for (const runSummary of state.scenarios.slice(0, 4)) {
      try {
        const full = await api(`/api/simulations/${runSummary.id}`);
        runSummary.valid_until = full.result?.valid_until;
        for (const trajectory of full.result?.trajectories || []) state.trajectories.push({...trajectory, run_id: full.id});
      } catch (error) { console.warn('Unable to load scenario run', runSummary.id, error); }
    }
    $('map-location').textContent = (state.location.name || 'SELECTED REGION').toUpperCase();
    $('release-lat').value = state.location.latitude;
    $('release-lon').value = state.location.longitude;
    $('obs-count').textContent = snapshot.observation_count ?? state.observations.length;
    $('ws-count').textContent = String(socketConnected ? 1 : 0);
    if (state.selectedTime > state.rangeEnd - 10_000) state.selectedTime = Date.now();
    updateTimelineBounds();
    rebuildCesiumSources();
    renderScenarios();
  } catch (error) {
    console.error(error);
    setConnection(false, `API ERROR · ${error.message.slice(0, 70)}`);
  }
}

let socketConnected = false;
let reconnectTimer = null;
let reconnectDelayMs = 1000;
let heartbeatTimer = null;
let lastPongAt = 0;
let lastTemporalFrame = 0;
let lastSliderUpdate = 0;
let socket;
function setConnection(online, text = online ? 'LIVE DATA CONNECTED' : 'API OFFLINE') {
  const element = document.querySelector('.connection');
  element.classList.toggle('online', online);
  element.classList.toggle('offline', !online);
  $('live-status').textContent = text;
}
function scheduleSocketReconnect() {
  if (reconnectTimer !== null) return;
  const jitter = Math.random() * 250;
  const wait = Math.min(30_000, reconnectDelayMs) + jitter;
  reconnectDelayMs = Math.min(30_000, reconnectDelayMs * 2);
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connectWebSocket();
  }, wait);
}
function connectWebSocket() {
  if (socket && (socket.readyState === WebSocket.CONNECTING || socket.readyState === WebSocket.OPEN)) return;
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  socket = new WebSocket(`${scheme}://${location.host}/ws`);
  socket.onopen = () => {
    socketConnected = true;
    reconnectDelayMs = 1000;
    lastPongAt = Date.now();
    if (heartbeatTimer !== null) clearInterval(heartbeatTimer);
    heartbeatTimer = setInterval(() => {
      if (!socket || socket.readyState !== WebSocket.OPEN) return;
      if (Date.now() - lastPongAt > 45_000) {
        setConnection(false, 'STREAM HEARTBEAT LOST · RECONNECTING');
        socket.close();
        return;
      }
      socket.send('ping');
    }, 20_000);
    setConnection(true);
    $('ws-count').textContent = '1';
  };
  socket.onclose = () => {
    socketConnected = false;
    if (heartbeatTimer !== null) clearInterval(heartbeatTimer);
    heartbeatTimer = null;
    setConnection(false, 'STREAM DISCONNECTED · RECONNECTING');
    $('ws-count').textContent = '0';
    scheduleSocketReconnect();
  };
  socket.onerror = () => socket.close();
  socket.onmessage = event => {
    try {
      const message = JSON.parse(event.data);
      if (message.type === 'pong') {
        lastPongAt = Date.now();
        return;
      }
      if (message.type === 'observation') {
        const item = message.data;
        if (!state.observations.some(existing => existing.id === item.id)) {
          state.observations.unshift(item);
          state.observations.length = Math.min(state.observations.length, 2500);
        }
        renderLists();
        rebuildCesiumSources();
      } else if (message.type === 'provider_status') {
        state.sourceStatus[message.source] = message.status;
        renderSourceHealth(state.sourceStatus);
      } else if (message.type === 'simulation_created') {
        refreshState();
      }
    } catch (error) { console.warn('Invalid event-stream message', error); }
  };
}

async function runSimulation() {
  const button = $('run-sim');
  button.disabled = true;
  showStatus($('simulation-status'), 'Running ensemble…');
  try {
    const releaseLatitude = Number($('release-lat').value);
    const releaseLongitude = Number($('release-lon').value);
    if (!Number.isFinite(releaseLatitude) || !Number.isFinite(releaseLongitude) ||
        releaseLatitude < -90 || releaseLatitude > 90 || releaseLongitude < -180 || releaseLongitude > 180) {
      throw new Error('Enter a valid WGS84 release latitude and longitude.');
    }
    const locationChanged = Math.abs(releaseLatitude - state.location.latitude) > 0.01 ||
      Math.abs(releaseLongitude - state.location.longitude) > 0.01;
    if (locationChanged) {
      showStatus($('simulation-status'), 'Refreshing live weather for the release location…');
      await updateLocation(releaseLatitude, releaseLongitude, 'Simulation release location');
    }
    const result = await api('/api/simulations/wind-advection', {
      method: 'POST',
      body: JSON.stringify({
        latitude: releaseLatitude, longitude: releaseLongitude,
        particle_count: Number($('particle-count').value), duration_minutes: Number($('duration').value),
        timestep_seconds: 60, title: 'Wind advection sensitivity run',
      }),
    });
    state.scenarios.unshift({id: result.id, title: 'Wind advection sensitivity run', model_name: result.model, created_at: result.created_at, valid_until: result.valid_until});
    for (const trajectory of result.trajectories || []) state.trajectories.push({...trajectory, run_id: result.id});
    showStatus($('simulation-status'), `${result.trajectories.length} trajectories · ${fmt(result.forcing.wind_speed_mps)} m/s from ${fmt(result.forcing.wind_from_deg, 0)}° · forcing offset ${fmt(result.forcing.release_to_forcing_distance_km, 1)} km`);
    renderScenarios();
    rebuildCesiumSources();
  } catch (error) {
    showStatus($('simulation-status'), error.message, true);
  } finally { button.disabled = false; }
}

async function updateLocation(latitude, longitude, name = 'Operator-selected location') {
  const result = await api('/api/location', {method: 'POST', body: JSON.stringify({latitude, longitude, name})});
  state.location = result.location;
  viewer.camera.flyTo({destination: Cesium.Cartesian3.fromDegrees(longitude, latitude, 500_000), duration: 1.3});
  deckViewState = {...deckViewState, longitude, latitude, zoom: 8, pitch: 0, bearing: 0};
  deck.setProps({viewState: deckViewState});
  $('map-location').textContent = name.toUpperCase();
  await refreshState();
}

$('refresh-btn').addEventListener('click', async () => {
  try { await api('/api/refresh', {method: 'POST'}); await refreshState(); }
  catch (error) { showStatus($('footer-source'), `Refresh failed: ${error.message}`, true); }
});
$('source-refresh').addEventListener('click', refreshState);
$('run-sim').addEventListener('click', runSimulation);
$('timeline').addEventListener('input', () => { state.playing = false; $('play-btn').textContent = '▶ PLAY'; setTimelineFromInput(); });
$('play-btn').addEventListener('click', () => { state.playing = !state.playing; $('play-btn').textContent = state.playing ? 'Ⅱ PAUSE' : '▶ PLAY'; $('play-btn').classList.toggle('active', state.playing); });
$('live-btn').addEventListener('click', () => { state.selectedTime = Date.now(); state.playing = false; $('play-btn').textContent = '▶ PLAY'; updateTimelineBounds(); updateTemporalVisibility(); updateDeck(true); });
$('step-back').addEventListener('click', () => { state.playing = false; state.selectedTime = Math.max(state.rangeStart, state.selectedTime - 3600_000); updateTimelineBounds(); updateTemporalVisibility(); updateDeck(true); });
$('step-forward').addEventListener('click', () => { state.playing = false; state.selectedTime = Math.min(state.rangeEnd, state.selectedTime + 3600_000); updateTimelineBounds(); updateTemporalVisibility(); updateDeck(true); });
$('playback-rate').addEventListener('change', event => {
  const rate = Number(event.target.value);
  if (Number.isFinite(rate) && rate > 0) state.playbackRate = rate;
});
$('home-view').addEventListener('click', () => viewer.camera.flyTo({destination: Cesium.Cartesian3.fromDegrees(0, 15, 19_000_000), duration: 1.2}));
$('terrain-toggle').addEventListener('click', () => {
  // Lighting is available without a third-party terrain account; elevation data remains a separate layer decision.
  const button = $('terrain-toggle'); button.classList.toggle('active');
  viewer.scene.globe.enableLighting = button.classList.contains('active');
  button.textContent = button.classList.contains('active') ? 'LIGHTING ON' : 'LIGHTING OFF';
});
for (const [id, key] of [['toggle-quakes', 'earthquakes'], ['toggle-alerts', 'alerts'], ['toggle-forecast', 'forecast'], ['toggle-sim', 'simulations']]) {
  $(id).addEventListener('click', () => {
    state.visible[key] = !state.visible[key];
    $(id).classList.toggle('active', state.visible[key]);
    updateTemporalVisibility();
    updateDeck(true);
  });
}
$('location-btn').addEventListener('click', () => {
  if (!navigator.geolocation) return showStatus($('footer-source'), 'Geolocation is not supported by this browser.', true);
  navigator.geolocation.getCurrentPosition(
    position => updateLocation(position.coords.latitude, position.coords.longitude, 'Browser geolocation').catch(error => showStatus($('footer-source'), error.message, true)),
    error => showStatus($('footer-source'), `Location permission/error: ${error.message}`, true),
    {enableHighAccuracy: true, timeout: 12000, maximumAge: 60_000},
  );
});
$('load-tileset').addEventListener('click', async () => {
  const url = $('tileset-url').value.trim();
  if (!url) return showStatus($('asset-status'), 'Enter a tileset URL first.', true);
  try {
    const tileset = await Cesium.Cesium3DTileset.fromUrl(url);
    viewer.scene.primitives.add(tileset);
    await viewer.zoomTo(tileset);
    showStatus($('asset-status'), `Loaded tileset: ${url}`);
  } catch (error) { showStatus($('asset-status'), `Tileset load failed: ${error.message}`, true); }
});

$('timeline').addEventListener('change', setTimelineFromInput);
viewer.screenSpaceEventHandler.setInputAction(movement => {
  const ray = viewer.camera.getPickRay(movement.position);
  const hit = viewer.scene.globe.pick(ray, viewer.scene);
  if (!hit) return;
  const cartographic = Cesium.Cartographic.fromCartesian(hit);
  $('map-coordinates').textContent = `WGS84 · ${Cesium.Math.toDegrees(cartographic.latitude).toFixed(5)}, ${Cesium.Math.toDegrees(cartographic.longitude).toFixed(5)}`;
}, Cesium.ScreenSpaceEventType.MOUSE_MOVE);

function animationLoop() {
  const now = performance.now();
  const dt = Math.min(0.25, (now - state.lastTick) / 1000);
  state.lastTick = now;
  if (state.playing) {
    // Playback rate is 5 simulated minutes per real second; data time remains UTC.
    state.selectedTime += dt * state.playbackRate * 60_000;
    if (state.selectedTime > state.rangeEnd) state.selectedTime = state.rangeStart;
    if (now - lastSliderUpdate > 1000) { updateTimelineBounds(); lastSliderUpdate = now; }
    if (now - lastTemporalFrame > 250) { updateTemporalVisibility(); updateDeck(); lastTemporalFrame = now; }
  }
  $('utc-clock').textContent = `${new Date().toISOString().slice(11, 19)} UTC`;
  requestAnimationFrame(animationLoop);
}

(async function boot() {
  connectWebSocket();
  await refreshState();
  setInterval(refreshState, 45_000);
  requestAnimationFrame(animationLoop);
})().catch(error => {
  console.error(error);
  setConnection(false, `BOOT ERROR · ${error.message.slice(0, 70)}`);
});

window.addEventListener('beforeunload', () => {
  if (reconnectTimer !== null) clearTimeout(reconnectTimer);
  if (heartbeatTimer !== null) clearInterval(heartbeatTimer);
  try { socket?.close(); } catch {}
  try { deck.finalize(); } catch {}
  try { viewer.destroy(); } catch {}
});
