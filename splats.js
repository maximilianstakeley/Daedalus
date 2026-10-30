import * as GaussianSplats3D from '@mkkellogg/gaussian-splats-3d';
import './style.css';

const form = document.getElementById('splat-form');
const input = document.getElementById('splat-url');
const status = document.getElementById('splat-status');
const root = document.getElementById('splat-viewport');
let viewer = null;

function setStatus(message, error = false) {
  status.textContent = message;
  status.classList.toggle('error', error);
}
function validAssetUrl(raw) {
  const url = new URL(raw, window.location.origin);
  if (!['http:', 'https:'].includes(url.protocol)) throw new Error('Only HTTP(S) asset URLs are supported.');
  const extension = url.pathname.split('.').pop().toLowerCase();
  if (!['ply', 'splat', 'ksplat'].includes(extension)) throw new Error('Supported formats: .ply, .splat and .ksplat.');
  return url.href;
}

async function loadScene(raw) {
  let url;
  try { url = validAssetUrl(raw); }
  catch (error) { setStatus(error.message, true); return; }
  try {
    if (viewer) {
      await viewer.dispose();
      viewer = null;
      root.replaceChildren();
    }
    setStatus(`Loading ${url} …`);
    viewer = new GaussianSplats3D.Viewer({
      rootElement: root,
      cameraUp: [0, 1, 0],
      initialCameraPosition: [0, 1.5, 5],
      initialCameraLookAt: [0, 0, 0],
      gpuAcceleratedSort: true,
      sharedMemoryForWorkers: false,
      logLevel: GaussianSplats3D.LogLevel.Warning,
    });
    await viewer.addSplatScene(url, {
      showLoadingUI: true,
      progressiveLoad: true,
      splatAlphaRemovalThreshold: 5,
      position: [0, 0, 0],
      rotation: [0, 0, 0, 1],
      scale: [1, 1, 1],
    });
    viewer.start();
    setStatus(`Loaded ${url}. Drag to orbit, right-drag to pan, scroll to zoom.`);
  } catch (error) {
    console.error(error);
    setStatus(`Load failed: ${error.message}. Check the asset path, file format, CORS, and browser GPU support.`, true);
  }
}

form.addEventListener('submit', event => {
  event.preventDefault();
  loadScene(input.value.trim());
});
const queryUrl = new URLSearchParams(window.location.search).get('url');
if (queryUrl) {
  input.value = queryUrl;
  loadScene(queryUrl);
}
