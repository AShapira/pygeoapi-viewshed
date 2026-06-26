import 'ol/ol.css';
import './style.css';

import Map from 'ol/Map.js';
import View from 'ol/View.js';
import Feature from 'ol/Feature.js';
import GeoJSON from 'ol/format/GeoJSON.js';
import ImageLayer from 'ol/layer/Image.js';
import VectorLayer from 'ol/layer/Vector.js';
import ImageWMS from 'ol/source/ImageWMS.js';
import VectorSource from 'ol/source/Vector.js';
import { Circle as CircleGeom, Point } from 'ol/geom.js';
import { Fill, Stroke, Style, Circle as CircleStyle } from 'ol/style.js';
import { defaults as defaultControls, ScaleLine } from 'ol/control.js';
import { get as getProjection } from 'ol/proj.js';
import { register } from 'ol/proj/proj4.js';
import proj4 from 'proj4';

proj4.defs('EPSG:32636', '+proj=utm +zone=36 +datum=WGS84 +units=m +no_defs +type=crs');
register(proj4);

const projection = getProjection('EPSG:32636');
projection.setExtent([645000, 3480000, 752500, 3605000]);

const DEFAULT_DEM_LAYER = 'dem:srtm_center_israel';
const DEM_EXTENT = [645532, 3480514, 751831, 3604500];
const DTM_METADATA = {
  layerName: DEFAULT_DEM_LAYER,
  projection: 'EPSG:32636 - WGS 84 / UTM zone 36N',
  resolution: '30 m / pixel',
  bbox: '34.55, 31.45, 35.65, 32.55',
};
const app = document.querySelector('#app');

app.innerHTML = `
  <main class="shell">
    <aside class="panel" aria-label="Viewshed controls">
      <div class="panel__header">
        <div>
          <h1>Viewshed Control</h1>
          <p>GDAL viewshed through pygeoapi and GeoServer</p>
        </div>
        <span id="service-status" class="status-dot" title="Service status"></span>
      </div>

      <form id="viewshed-form" class="form">
        <label>
          <span>DEM layer</span>
          <input id="demLayer" name="demLayer" value="${escapeHtml(DEFAULT_DEM_LAYER)}" autocomplete="off" />
        </label>

        <section class="dtm-info" aria-label="DTM metadata">
          <h2>DTM</h2>
          <dl class="metadata-list">
            <dt>Layer</dt><dd>${escapeHtml(DTM_METADATA.layerName)}</dd>
            <dt>Projection</dt><dd>${escapeHtml(DTM_METADATA.projection)}</dd>
            <dt>Resolution</dt><dd>${escapeHtml(DTM_METADATA.resolution)}</dd>
            <dt>BBox</dt><dd>${escapeHtml(DTM_METADATA.bbox)}</dd>
          </dl>
        </section>

        <div class="grid-2">
          <label>
            <span>Observer X</span>
            <input id="observerX" name="observerX" type="number" step="1" value="703000" />
          </label>
          <label>
            <span>Observer Y</span>
            <input id="observerY" name="observerY" type="number" step="1" value="3497000" />
          </label>
        </div>

        <div class="grid-2">
          <label>
            <span>Observer height</span>
            <input id="observerHeight" name="observerHeight" type="number" step="0.1" min="0" value="2" />
          </label>
          <label>
            <span>Target height</span>
            <input id="targetHeight" name="targetHeight" type="number" step="0.1" min="0" value="0" />
          </label>
        </div>

        <label>
          <span>Max distance</span>
          <input id="maxDistance" name="maxDistance" type="number" step="1" min="1" max="5000" value="3000" />
        </label>

        <label>
          <span>Refraction coefficient</span>
          <input id="refractionCoefficient" name="refractionCoefficient" type="number" step="0.000001" min="0" max="1" value="0.142857" />
        </label>

        <label>
          <span>Output name</span>
          <input id="outputName" name="outputName" autocomplete="off" />
        </label>

        <fieldset>
          <legend>Output type</legend>
          <div class="segmented">
            <label><input type="radio" name="outputType" value="raster" /> <span>Raster</span></label>
            <label><input type="radio" name="outputType" value="vector" /> <span>Vector</span></label>
            <label><input type="radio" name="outputType" value="both" checked /> <span>Both</span></label>
          </div>
        </fieldset>

        <button id="run-button" class="run-button" type="submit">Run viewshed</button>
      </form>

      <section class="message" id="message" aria-live="polite"></section>

      <section class="results">
        <h2>Result</h2>
        <dl id="result-summary"></dl>
        <div id="result-links" class="links"></div>
      </section>
    </aside>

    <section class="map-wrap" aria-label="Viewshed map">
      <div id="map"></div>
      <div class="map-tools">
        <button id="zoom-dem" type="button">DEM extent</button>
        <label><input id="show-raster" type="checkbox" checked /> Raster</label>
        <label><input id="show-vector" type="checkbox" checked /> Vector</label>
      </div>
    </section>
  </main>
`;

const demLayer = new ImageLayer({
  source: new ImageWMS({
    url: '/geoserver/wms',
    params: {
      LAYERS: DEFAULT_DEM_LAYER,
      FORMAT: 'image/png',
      TRANSPARENT: true,
      VERSION: '1.1.1',
    },
    ratio: 1,
    serverType: 'geoserver',
  }),
  opacity: 0.55,
});

const rasterResultLayer = new ImageLayer({ visible: true, opacity: 0.72 });
const vectorSource = new VectorSource();
const vectorResultLayer = new VectorLayer({
  source: vectorSource,
  visible: true,
  style: (feature) => {
    const visible = Number(feature.get('Visible') ?? feature.get('visible')) === 1;
    return new Style({
      fill: new Fill({ color: visible ? 'rgba(15, 157, 88, 0.36)' : 'rgba(94, 105, 114, 0.22)' }),
      stroke: new Stroke({ color: visible ? 'rgba(10, 126, 70, 0.95)' : 'rgba(78, 91, 101, 0.7)', width: 1 }),
    });
  },
});

const observerSource = new VectorSource();
const observerLayer = new VectorLayer({
  source: observerSource,
  style: (feature) => {
    if (feature.get('kind') === 'radius') {
      return new Style({
        fill: new Fill({ color: 'rgba(36, 99, 235, 0.08)' }),
        stroke: new Stroke({ color: 'rgba(36, 99, 235, 0.85)', width: 2 }),
      });
    }
    return new Style({
      image: new CircleStyle({
        radius: 6,
        fill: new Fill({ color: '#dc2626' }),
        stroke: new Stroke({ color: '#ffffff', width: 2 }),
      }),
    });
  },
});

const map = new Map({
  target: 'map',
  controls: defaultControls({ attribution: false }).extend([new ScaleLine()]),
  layers: [demLayer, rasterResultLayer, vectorResultLayer, observerLayer],
  view: new View({
    projection,
    center: [703000, 3542500],
    zoom: 2,
    resolutions: [120, 60, 30, 15, 7.5, 3.75, 1.875],
    extent: [645000, 3480000, 752500, 3605000],
  }),
});

map.getView().fit(DEM_EXTENT, { padding: [48, 48, 48, 48], duration: 0 });

const form = document.querySelector('#viewshed-form');
const message = document.querySelector('#message');
const runButton = document.querySelector('#run-button');
const statusDot = document.querySelector('#service-status');
const resultSummary = document.querySelector('#result-summary');
const resultLinks = document.querySelector('#result-links');

const observerXInput = document.querySelector('#observerX');
const observerYInput = document.querySelector('#observerY');
const maxDistanceInput = document.querySelector('#maxDistance');
const outputNameInput = document.querySelector('#outputName');
outputNameInput.value = `viewshed_ui_${timestamp()}`;

document.querySelector('#zoom-dem').addEventListener('click', () => {
  map.getView().fit(DEM_EXTENT, { padding: [48, 48, 48, 48], duration: 180 });
});
document.querySelector('#show-raster').addEventListener('change', (event) => {
  rasterResultLayer.setVisible(event.target.checked);
});
document.querySelector('#show-vector').addEventListener('change', (event) => {
  vectorResultLayer.setVisible(event.target.checked);
});

[observerXInput, observerYInput, maxDistanceInput].forEach((input) => {
  input.addEventListener('input', updateObserverGraphics);
});

map.on('singleclick', (event) => {
  observerXInput.value = Math.round(event.coordinate[0]);
  observerYInput.value = Math.round(event.coordinate[1]);
  updateObserverGraphics();
});

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  await runViewshed();
});

updateObserverGraphics();
checkService();

async function checkService() {
  try {
    const response = await fetch('/processes/viewshed');
    statusDot.classList.toggle('status-dot--ok', response.ok);
    statusDot.title = response.ok ? 'pygeoapi process reachable' : 'pygeoapi process unavailable';
  } catch {
    statusDot.classList.remove('status-dot--ok');
    statusDot.title = 'pygeoapi process unavailable';
  }
}

async function runViewshed() {
  const inputs = readInputs();
  if (!inputs.outputName) {
    inputs.outputName = `viewshed_ui_${timestamp()}`;
    outputNameInput.value = inputs.outputName;
  }

  setBusy(true);
  showMessage('Running viewshed...', 'info');
  resultSummary.innerHTML = '';
  resultLinks.innerHTML = '';

  try {
    const response = await fetch('/processes/viewshed/execution', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ inputs }),
    });
    const body = await response.json();
    if (!response.ok) {
      throw new Error(body.description || body.message || `Process failed with HTTP ${response.status}`);
    }
    applyResult(body.result);
    showMessage('Viewshed published.', 'success');
    outputNameInput.value = `viewshed_ui_${timestamp()}`;
  } catch (error) {
    showMessage(error.message, 'error');
  } finally {
    setBusy(false);
  }
}

function readInputs() {
  const formData = new FormData(form);
  const refractionRaw = String(formData.get('refractionCoefficient') ?? '').trim();
  const inputs = {
    demLayer: String(formData.get('demLayer') || DEFAULT_DEM_LAYER),
    observerX: Number(formData.get('observerX')),
    observerY: Number(formData.get('observerY')),
    observerHeight: Number(formData.get('observerHeight')),
    targetHeight: Number(formData.get('targetHeight')),
    maxDistance: Number(formData.get('maxDistance')),
    outputName: String(formData.get('outputName') || '').trim(),
    outputType: String(formData.get('outputType') || 'both'),
  };
  if (refractionRaw !== '') {
    inputs.refractionCoefficient = Number(refractionRaw);
  }
  return inputs;
}

function applyResult(result) {
  resultSummary.innerHTML = `
    <dt>Status</dt><dd>${escapeHtml(result.status)}</dd>
    <dt>Output</dt><dd>${escapeHtml(result.outputType)}</dd>
    <dt>Workspace</dt><dd>${escapeHtml(result.workspace)}</dd>
    <dt>Refraction</dt><dd>${result.refractionCoefficient ?? 'GDAL default'}</dd>
  `;

  const links = [];
  if (result.raster) {
    const rasterLayerName = result.raster.layerName;
    rasterResultLayer.setSource(new ImageWMS({
      url: '/geoserver/wms',
      params: {
        LAYERS: rasterLayerName,
        FORMAT: 'image/png',
        TRANSPARENT: true,
        VERSION: '1.1.1',
      },
      ratio: 1,
      serverType: 'geoserver',
    }));
    rasterResultLayer.setVisible(document.querySelector('#show-raster').checked);
    links.push(link('Raster WMS', result.raster.wmsUrl));
    links.push(link('Raster WCS', result.raster.wcsUrl));
  }

  if (result.vector) {
    loadVectorResult(result.vector.wfsUrl);
    vectorResultLayer.setVisible(document.querySelector('#show-vector').checked);
    links.push(link('Vector WMS', result.vector.wmsUrl));
    links.push(link('Vector WFS', result.vector.wfsUrl));
  } else {
    vectorSource.clear();
  }

  resultLinks.innerHTML = links.join('');
  if (Array.isArray(result.bbox)) {
    map.getView().fit(result.bbox, { padding: [56, 56, 56, 56], duration: 240 });
  }
}

async function loadVectorResult(wfsUrl) {
  const url = sameOriginUrl(wfsUrl);
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Vector WFS failed with HTTP ${response.status}`);
  }
  const geojson = await response.json();
  const features = new GeoJSON().readFeatures(geojson, {
    dataProjection: 'EPSG:32636',
    featureProjection: 'EPSG:32636',
  });
  vectorSource.clear();
  vectorSource.addFeatures(features);
}

function updateObserverGraphics() {
  const x = Number(observerXInput.value);
  const y = Number(observerYInput.value);
  const distance = Number(maxDistanceInput.value);
  if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(distance)) {
    return;
  }
  observerSource.clear();
  const point = new Feature({ geometry: new Point([x, y]), kind: 'observer' });
  const radius = new Feature({ geometry: new CircleGeom([x, y], distance), kind: 'radius' });
  observerSource.addFeatures([radius, point]);
}

function sameOriginUrl(url) {
  try {
    const parsed = new URL(url, window.location.origin);
    return `${parsed.pathname}${parsed.search}`;
  } catch {
    return url;
  }
}

function setBusy(isBusy) {
  runButton.disabled = isBusy;
  runButton.textContent = isBusy ? 'Running...' : 'Run viewshed';
}

function showMessage(text, kind) {
  message.className = `message message--${kind}`;
  message.textContent = text;
}

function link(label, url) {
  return `<a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${escapeHtml(label)}</a>`;
}

function timestamp() {
  return new Date().toISOString().replace(/[-:TZ.]/g, '').slice(0, 14);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}
