import * as THREE from 'three';
import {PLYLoader} from 'three/addons/loaders/PLYLoader.js';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import './style.css';

const $ = id => document.getElementById(id);
const viewport = $('reconstruction-canvas');
const status = $('reconstruction-status');
const meta = $('reconstruction-meta');
const slider = $('snapshot-slider');
const renderer = new THREE.WebGLRenderer({antialias: true, powerPreference: 'high-performance'});
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
renderer.setSize(viewport.clientWidth, viewport.clientHeight);
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.0;
viewport.appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x071019);
const camera = new THREE.PerspectiveCamera(55, viewport.clientWidth / viewport.clientHeight, 0.001, 1e7);
camera.position.set(3, 2.5, 4);
scene.add(new THREE.HemisphereLight(0xc9e6ff, 0x263342, 1.6));
const keyLight = new THREE.DirectionalLight(0xffffff, 2.1);
keyLight.position.set(5, 8, 6);
scene.add(keyLight);
const fillLight = new THREE.DirectionalLight(0x71c9d3, 0.65);
fillLight.position.set(-5, 2, -4);
scene.add(fillLight);
const axes = new THREE.AxesHelper(1);
scene.add(axes);
const grid = new THREE.GridHelper(10, 20, 0x37556b, 0x20384a);
grid.position.y = -0.0005;
scene.add(grid);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.075;
controls.screenSpacePanning = true;
controls.target.set(0, 0, 0);
const loader = new PLYLoader();
let snapshots = [];
let selectedIndex = 0;
let currentMesh = null;
let playbackTimer = null;
let loadSequence = 0;

function setStatus(message, isError = false) {
  status.textContent = message;
  status.classList.toggle('error', isError);
}
function timeLabel(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'time unavailable' : date.toISOString().replace('T', ' ').replace('Z', ' UTC');
}
function clearCurrentMesh() {
  if (!currentMesh) return;
  scene.remove(currentMesh);
  currentMesh.geometry.dispose();
  if (Array.isArray(currentMesh.material)) currentMesh.material.forEach(material => material.dispose());
  else currentMesh.material.dispose();
  currentMesh = null;
}
function frameDescription(snapshot) {
  const interval = `${timeLabel(snapshot.start_time)} → ${timeLabel(snapshot.end_time)}`;
  const counts = `${Number(snapshot.vertex_count || 0).toLocaleString()} vertices · ${Number(snapshot.triangle_count || 0).toLocaleString()} triangles`;
  return `${interval}\n${snapshot.coordinate_frame || 'frame not declared'} · ${snapshot.translation_units || 'units not declared'} · ${counts}\n${snapshot.registration_quality || 'registration quality not supplied'} · SHA-256 ${snapshot.mesh_sha256 || 'not supplied'}`;
}
function fitCamera(geometry) {
  geometry.computeBoundingBox();
  const box = geometry.boundingBox;
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  const largest = Math.max(size.x, size.y, size.z, 0.1);
  controls.target.copy(center);
  camera.near = Math.max(largest / 10000, 0.0001);
  camera.far = Math.max(largest * 100, 100);
  camera.position.copy(center).add(new THREE.Vector3(largest * 1.4, largest * 0.9, largest * 1.7));
  camera.updateProjectionMatrix();
  controls.update();
  grid.scale.setScalar(Math.max(1, Math.ceil(largest / 10)));
  axes.scale.setScalar(Math.max(0.1, largest * 0.08));
}
async function loadSnapshot(index) {
  if (!snapshots.length) return;
  // Invalidate older in-flight fetches even when the newly selected frame has
  // no asset, so a slow previous request cannot overwrite the current selection.
  const thisLoad = ++loadSequence;
  selectedIndex = Math.max(0, Math.min(snapshots.length - 1, index));
  slider.value = String(selectedIndex);
  const snapshot = snapshots[selectedIndex];
  $('snapshot-time').textContent = `FRAME ${String(selectedIndex + 1).padStart(3, '0')} · ${timeLabel(snapshot.start_time)}`;
  meta.textContent = frameDescription(snapshot);
  if (!snapshot.mesh_url || snapshot.asset_status !== 'available') {
    setStatus(`Mesh asset unavailable for ${snapshot.id || `snapshot ${selectedIndex}`}. Check the reconstruction output directory.`, true);
    clearCurrentMesh();
    return;
  }
  setStatus(`Loading ${snapshot.mesh}…`);
  try {
    const response = await fetch(snapshot.mesh_url, {cache: 'no-cache'});
    if (!response.ok) throw new Error(`HTTP ${response.status} ${response.statusText}`);
    const buffer = await response.arrayBuffer();
    if (thisLoad !== loadSequence) return;
    if (snapshot.mesh_sha256 && globalThis.crypto?.subtle) {
      const digest = await crypto.subtle.digest('SHA-256', buffer);
      const actual = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
      if (actual.toLowerCase() !== String(snapshot.mesh_sha256).toLowerCase()) {
        throw new Error('Mesh SHA-256 checksum mismatch; refusing to display a corrupted or changed asset.');
      }
    }
    if (thisLoad !== loadSequence) return;
    const geometry = loader.parse(buffer);
    if (!geometry.getAttribute('position') || geometry.getAttribute('position').count === 0) {
      geometry.dispose();
      throw new Error('The PLY mesh contains no vertices.');
    }
    if (!geometry.getAttribute('normal')) geometry.computeVertexNormals();
    geometry.computeBoundingBox();
    const material = new THREE.MeshStandardMaterial({
      color: 0xffffff,
      vertexColors: Boolean(geometry.getAttribute('color')),
      roughness: 0.88,
      metalness: 0.02,
      side: THREE.DoubleSide,
    });
    const mesh = new THREE.Mesh(geometry, material);
    mesh.frustumCulled = true;
    clearCurrentMesh();
    currentMesh = mesh;
    scene.add(mesh);
    fitCamera(geometry);
    setStatus(`Snapshot ${selectedIndex + 1}/${snapshots.length} loaded · ${snapshot.frame_count} RGB-D frames fused.`);
  } catch (error) {
    if (thisLoad !== loadSequence) return;
    console.error(error);
    setStatus(`Could not load reconstruction: ${error.message}`, true);
  }
}
function stopPlayback() {
  if (playbackTimer !== null) window.clearInterval(playbackTimer);
  playbackTimer = null;
  $('play-frames').textContent = '▶ PLAY';
}
function startPlayback() {
  if (snapshots.length < 2) return;
  stopPlayback();
  $('play-frames').textContent = 'Ⅱ PAUSE';
  playbackTimer = window.setInterval(() => {
    if (selectedIndex >= snapshots.length - 1) {
      stopPlayback();
      return;
    }
    loadSnapshot(selectedIndex + 1);
  }, 1000);
}
async function boot() {
  try {
    const response = await fetch('/api/reconstructions/timeline');
    if (response.status === 404) {
      setStatus('No reconstruction timeline found. Capture calibrated RGB-D frames and run the Open3D reconstruction worker.');
      return;
    }
    if (!response.ok) throw new Error(`API returned ${response.status}`);
    const manifest = await response.json();
    snapshots = (manifest.snapshots || []).slice().sort((a, b) => new Date(a.start_time) - new Date(b.start_time));
    $('snapshot-count').textContent = `${snapshots.length} SNAPSHOTS`;
    if (!snapshots.length) {
      setStatus('The reconstruction manifest exists but contains no non-empty mesh snapshots.');
      return;
    }
    slider.disabled = false;
    slider.max = String(snapshots.length - 1);
    $('timeline-first').textContent = `FIRST · ${timeLabel(snapshots[0].start_time)}`;
    $('timeline-last').textContent = `LAST · ${timeLabel(snapshots.at(-1).end_time)}`;
    setStatus(`Loaded ${snapshots.length} time-indexed mesh snapshots · ${manifest.coordinate_frame || 'frame not declared'}.`);
    await loadSnapshot(0);
  } catch (error) {
    console.error(error);
    setStatus(`Could not load reconstruction manifest: ${error.message}`, true);
  }
}

slider.addEventListener('input', () => { stopPlayback(); loadSnapshot(Number(slider.value)); });
$('previous-frame').addEventListener('click', () => { stopPlayback(); loadSnapshot(selectedIndex - 1); });
$('next-frame').addEventListener('click', () => { stopPlayback(); loadSnapshot(selectedIndex + 1); });
$('play-frames').addEventListener('click', () => playbackTimer === null ? startPlayback() : stopPlayback());
window.addEventListener('resize', () => {
  const width = Math.max(1, viewport.clientWidth);
  const height = Math.max(1, viewport.clientHeight);
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
  renderer.setSize(width, height);
});
window.addEventListener('beforeunload', () => {
  stopPlayback();
  clearCurrentMesh();
  controls.dispose();
  renderer.dispose();
});
function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}
requestAnimationFrame(animate);
boot();
