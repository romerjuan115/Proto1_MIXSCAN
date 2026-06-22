// ── DRONE DETECTOR GUI — app.js ──
const SVG_SHIELD = `<svg class="banner-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2L4 6v6c0 5.5 3.5 10.74 8 12 4.5-1.26 8-6.5 8-12V6L12 2z"/><polyline points="9,12 11,14 15,10"/></svg>`;
const SVG_PULSE  = `<svg class="banner-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="2,12 5,12 7,5 10,19 13,7 16,12 19,12 22,12"/></svg>`;
const SVG_WARN   = `<svg class="banner-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`;

function fmtMavTs(raw) {
  if (!raw) return '—';
  const d = new Date(raw.replace(' UTC', '').replace(' ', 'T') + 'Z');
  if (isNaN(d.getTime())) return raw;
  return d.toLocaleString('es-MX', {
    hour12: false, year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit'
  });
}

let currentMode = 'proto1';
let chartBar = null, chartScatter = null;
let loadedData = [];
let isLive = false;
let liveInterval = null;
let lastFileHash = '';
let mavlinkCount = 0;
let bootMavlinkStatus = 'pending'; // 'pending' | 'connected'

// ── CAPA 2 MAVLink ──
function parseMavlinkCSV(text) {
  return text.trim().split('\n')
    .filter(l => l.trim() && !l.startsWith('timestamp'))
    .map(line => {
      const cols = line.split(',').map(c => c.trim());
      return {
        timestamp:    cols[0] || '',
        msg_id:       parseInt(cols[1]) || 0,
        snr:          (cols[2] === 'N/A' || cols[2] === '' || isNaN(parseFloat(cols[2]))) ? null : parseFloat(cols[2]),
        fuente:       cols[3] || '',
        armed:        cols.length > 4 ? (cols[4] === 'True' || cols[4] === 'true') : null,
        vehicle_type: cols.length > 5 && cols[5] !== 'N/A' ? cols[5] : '',
        autopilot:    cols.length > 6 && cols[6] !== 'N/A' ? cols[6] : '',
      };
    });
}

function updateCapa2Panel(heartbeats) {
  bootMavlinkStatus = 'connected';
  mavlinkCount += heartbeats.length;
  const last = heartbeats[heartbeats.length - 1];

  document.getElementById('capa2-count').textContent = mavlinkCount;
  const estado = document.getElementById('capa2-estado');
  estado.textContent = 'HEARTBEAT CONFIRMADO';
  estado.classList.add('confirmed');
  const iconWrap = document.getElementById('capa2-icon-wrap');
  iconWrap.classList.add('confirmed');
  const card = document.getElementById('capa2-card');
  card.classList.remove('lost');
  card.classList.add('confirmed');
  document.getElementById('capa2-estado').classList.remove('lost');

  // Estado ARMADO / DESARMADO
  const armedEl = document.getElementById('capa2-armed');
  if (armedEl && last.armed !== null) {
    armedEl.innerHTML = last.armed
      ? '<span class="badge badge-danger">ARMADO</span>'
      : '<span class="badge badge-clear">DESARMADO</span>';
    armedEl.style.display = '';
  }

  // Tipo de vehículo y autopilot en el subtítulo
  if (last.vehicle_type || last.autopilot) {
    const sub = document.getElementById('capa2-sub');
    if (sub) {
      const parts = ['COM8 · SiK'];
      if (last.vehicle_type) parts.push(last.vehicle_type);
      if (last.autopilot)    parts.push(last.autopilot);
      sub.textContent = parts.join(' · ');
    }
  }

  const lastWrap = document.getElementById('capa2-last-wrap');
  lastWrap.style.display = '';
  document.getElementById('capa2-snr').textContent =
    last.snr !== null
      ? `Δ SNR ref: ${last.snr >= 0 ? '+' : ''}${last.snr.toFixed(2)} dB`
      : 'SNR: N/A';
  document.getElementById('capa2-ts').textContent = fmtMavTs(last.timestamp);
}

function resetCapa2Panel() {
  mavlinkCount = 0;
  document.getElementById('capa2-count').textContent = '0';
  const estado = document.getElementById('capa2-estado');
  estado.textContent = 'EN ESPERA';
  estado.classList.remove('confirmed', 'lost');
  const iconWrap = document.getElementById('capa2-icon-wrap');
  iconWrap.classList.remove('confirmed');
  const card = document.getElementById('capa2-card');
  card.classList.remove('confirmed', 'lost');
  document.getElementById('capa2-last-wrap').style.display = 'none';
  const armedEl = document.getElementById('capa2-armed');
  if (armedEl) armedEl.style.display = 'none';
  const sub = document.getElementById('capa2-sub');
  if (sub) sub.textContent = 'COM8 · SiK · 57,600 bd · MAVLink HEARTBEAT';
}

function handleMavlinkLost(elapsed) {
  const estado  = document.getElementById('capa2-estado');
  const iconWrap = document.getElementById('capa2-icon-wrap');
  const card    = document.getElementById('capa2-card');
  const armedEl = document.getElementById('capa2-armed');
  if (estado)   { estado.textContent = 'SEÑAL PERDIDA'; estado.classList.remove('confirmed'); estado.classList.add('lost'); }
  if (iconWrap) iconWrap.classList.remove('confirmed');
  if (card)     { card.classList.remove('confirmed'); card.classList.add('lost'); }
  if (armedEl)  armedEl.style.display = 'none';
}

function showCorrelationBanner(confianza, armed) {
  const banner = document.getElementById('alert-banner');
  banner.className = 'alert-banner danger';
  banner.innerHTML = `${SVG_WARN}
    <div class="alert-text">
      <h2>DRON CONFIRMADO — ${confianza}% CONFIANZA</h2>
      <p>Capa 1 (SDR/RF) + Capa 2 (MAVLink${armed ? ' · <strong>ARMADO</strong>' : ''}) detectaron simultáneamente</p>
    </div>`;
  document.getElementById('radar-dot').classList.add('active');
}

// ── MODE SWITCH ──
function switchMode(mode) {
  currentMode = mode;
  document.querySelectorAll('.mode-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('btn-' + mode).classList.add('active');

  resetCapa2Panel();

  // Reset
  loadedData = [];
  lastFileHash = '';
  document.getElementById('dashboard').classList.add('hidden');
  document.getElementById('load-section').classList.remove('hidden');
  resetAlert();

  // If live is active, trigger immediate update
  if (isLive) {
    pollLiveServer();
  }
}

// ── LIVE POLLING ──
let ws = null;

function connectWebSocket() {
  if (ws) return;
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${protocol}//${window.location.host}/ws`);
  
  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      if (msg.type === 'new_data' && currentMode === 'proto1') {
        const fakeHeader = "archivo,delta_snr,coseno,resultado,confianza,timestamp\n";
        const newData = parseCSV(fakeHeader + msg.data);
        if (newData.length > 0) {
          loadedData = [...loadedData, ...newData];
          renderDashboard();
        }
      } else if (msg.type === 'mavlink_lost' && currentMode === 'proto1') {
        handleMavlinkLost(msg.elapsed);
      } else if (msg.type === 'correlation_alert' && currentMode === 'proto1') {
        showCorrelationBanner(msg.confianza, msg.armed);
      } else if (msg.type === 'mavlink_heartbeat' && currentMode === 'proto1') {
        const rows = parseMavlinkCSV(msg.data);
        const heartbeats = rows.filter(r => r.msg_id === 0 && r.fuente === 'serial');
        if (heartbeats.length > 0) {
          updateCapa2Panel(heartbeats);
        }
      }
    } catch (e) {
      console.error("WS Error:", e);
    }
  };

  ws.onclose = () => {
    ws = null;
    if (isLive) setTimeout(connectWebSocket, 1000);
  };
}

function disconnectWebSocket() {
  if (ws) {
    ws.close();
    ws = null;
  }
}

function updateLiveToggleUI() {
  const stop        = document.getElementById('btn-live-stop');
  const cta         = document.getElementById('splash-cta-wrap');
  const splashShown = !document.getElementById('load-section').classList.contains('hidden');

  if (isLive) {
    // Detección activa → header muestra DETENER en rojo
    if (stop) {
      stop.style.display = '';
      stop.innerHTML = '<span class="live-dot-pulse"></span> EN VIVO — DETENER';
      stop.className = 'btn-load btn-live-stop';
    }
    if (cta) cta.style.display = 'none';
  } else if (splashShown) {
    // Splash visible → header limpio, hero button maneja el inicio
    if (stop) stop.style.display = 'none';
    if (cta)  cta.style.display  = '';
  } else {
    // Dashboard visible, sin detección → header muestra INICIAR en cyan
    if (stop) {
      stop.style.display = '';
      stop.textContent = 'INICIAR DETECCIÓN';
      stop.className = 'btn-load btn-live-idle';
    }
    if (cta) cta.style.display = 'none';
  }
}

async function toggleLive() {
  const hero = document.getElementById('btn-live-hero');
  const stop = document.getElementById('btn-live-stop');

  if (!isLive) {
    try {
      if (hero) hero.style.opacity = '0.6';
      if (stop) stop.style.opacity = '0.6';
      const res = await fetch('/api/start-detection', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: 'proto1' })
      });
      let data = null;
      try { data = await res.json(); } catch (e) {}

      if (!res.ok) throw new Error(data && data.message ? data.message : 'Error en el servidor');
      if (data && data.status === 'error') throw new Error(data.message);

      loadedData = [];
      lastFileHash = '';
      if (currentMode === 'proto1') {
        resetCapa2Panel();
        renderDashboard();
      }

      isLive = true;
      updateLiveToggleUI();
      pollLiveServer();
      connectWebSocket();
      if (!liveInterval) {
        liveInterval = setInterval(pollLiveServer, 1500);
      }
    } catch (err) {
      alert('Error al activar PlutoSDR/Watcher: ' + err.message);
      isLive = false;
      updateLiveToggleUI();
    } finally {
      if (hero) hero.style.opacity = '1';
      if (stop) stop.style.opacity = '1';
    }
  } else {
    try {
      if (stop) { stop.style.opacity = '0.7'; stop.innerHTML = '<span class="live-dot-pulse"></span> DETENIENDO...'; }
      const res = await fetch('/api/stop-detection', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: 'proto1' })
      });
      let data = null;
      try { data = await res.json(); } catch (e) {}

      if (!res.ok) throw new Error(data && data.message ? data.message : 'Error en el servidor');
      if (data && data.status === 'error') throw new Error(data.message);

      isLive = false;
      updateLiveToggleUI();
      disconnectWebSocket();
      if (liveInterval) {
        clearInterval(liveInterval);
        liveInterval = null;
      }
    } catch (err) {
      alert('Error al desactivar PlutoSDR/Watcher: ' + err.message);
      isLive = true;
      updateLiveToggleUI();
    } finally {
      if (stop) stop.style.opacity = '1';
    }
  }
}

async function checkInitialDetectionStatus() {
  try {
    const res = await fetch('/api/detection-status');
    if (!res.ok) throw new Error('No se pudo obtener el estado');
    const data = await res.json();

    if (data.captura === 'running' || data.watcher === 'running') {
      // ── Proto1 SDR + MAVLink estaba corriendo ────────────────────────────
      isLive = true;
      updateLiveToggleUI();
      pollLiveServer();
      connectWebSocket();
      if (!liveInterval) {
        liveInterval = setInterval(pollLiveServer, 1500);
      }
    } else {
      isLive = false;
      updateLiveToggleUI();
    }
  } catch (err) {
    console.error('Error de comunicación con servidor:', err);
    isLive = false;
    updateLiveToggleUI();
  }
}

// Automatically fetch from local HTTP server in real time
async function pollLiveServer() {
  try {
    const res = await fetch('../resultados.csv?t=' + Date.now());
    if (!res.ok) throw new Error('No se pudo encontrar resultados.csv');
    const text = await res.text();

    if (text === lastFileHash) return;
    lastFileHash = text;

    loadedData = parseCSV(text);
    if (loadedData.length > 0) {
      renderDashboard();
    }
  } catch (err) {
    console.warn('Auto-Sync: ' + err.message);
  }
}

function parseCSV(text) {
  const lines = text.trim().split('\n');
  if (lines.length < 2) return [];
  const headers = lines[0].split(',').map(h => h.trim());
  return lines.slice(1).filter(l => l.trim()).map(line => {
    const cols = line.split(',').map(c => c.trim());
    return {
      archivo: cols[0] || '',
      delta_snr: parseFloat(cols[1]) || 0,
      coseno: parseFloat(cols[2]) || 0,
      resultado: cols[3] || '',
      confianza: parseInt(cols[4]) || 0,
      timestamp: cols[5] || ''
    };
  });
}

// ── RENDER ──
function renderDashboard() {
  stopMetricsPolling();
  document.getElementById('load-section').classList.add('hidden');
  document.getElementById('dashboard').classList.remove('hidden');

  const upd = document.getElementById('last-update');
  if (upd) {
    upd.textContent = 'act. ' + new Date().toLocaleTimeString('es-MX', { hour12: false });
    upd.style.display = '';
  }

  updateAlert();
  updateKPIs();
  buildCharts();
  buildTable();
}

// ── ALERT ──
function updateAlert() {
  const banner = document.getElementById('alert-banner');
  const radarDot = document.getElementById('radar-dot');
  let hasDrone = false;

  // Solo el clip más reciente determina el estado actual
  const last = loadedData[loadedData.length - 1];
  hasDrone = last && last.resultado === 'DRON DETECTADO';

  if (hasDrone) {
    const last = loadedData[loadedData.length - 1];
    const totalDet = loadedData.filter(r => r.resultado === 'DRON DETECTADO').length;
    banner.className = 'alert-banner danger';
    banner.innerHTML = `${SVG_WARN}
      <div class="alert-text">
        <h2>DRON DETECTADO</h2>
        <p>${totalDet} detección${totalDet !== 1 ? 'es' : ''} en ${loadedData.length} capturas · última: ${last.timestamp || 'ahora'}</p>
      </div>`;
    radarDot.classList.add('active');
  } else {
    const totalDet = loadedData.filter(r => r.resultado === 'DRON DETECTADO').length;
    const msg = totalDet > 0
      ? `${totalDet} detección${totalDet !== 1 ? 'es' : ''} previa${totalDet !== 1 ? 's' : ''} en sesión — última captura limpia`
      : 'No se detectó actividad de dron — ambiente seguro';
    banner.className = 'alert-banner clear';
    banner.innerHTML = `${SVG_SHIELD}
      <div class="alert-text">
        <h2>ESPACIO AÉREO LIMPIO</h2>
        <p>${msg}</p>
      </div>`;
    radarDot.classList.remove('active');
  }
}

function resetAlert() {
  const banner = document.getElementById('alert-banner');
  banner.className = 'alert-banner waiting';
  banner.innerHTML = `${SVG_PULSE}
    <div class="alert-text">
      <h2>ESPERANDO DATOS</h2>
      <p>Inicia la detección en vivo para comenzar el análisis</p>
    </div>`;
  document.getElementById('radar-dot').classList.remove('active');
}

// ── KPIs ──
function updateKPIs() {
  const n = loadedData.length;
  const snrs = loadedData.map(r => r.delta_snr);
  const maxSnr = n > 0 ? Math.max(...snrs) : null;
  const avgCos = n > 0 ? (loadedData.reduce((a, r) => a + r.coseno, 0) / n).toFixed(4) : null;

  document.getElementById('kpi-total').textContent = n;
  document.getElementById('kpi-snr-max').textContent =
    maxSnr !== null && isFinite(maxSnr) ? (maxSnr >= 0 ? '+' : '') + maxSnr.toFixed(2) + ' dB' : '—';

  const detKpi   = document.getElementById('kpi-det').closest('.kpi');
  const cleanKpi = document.getElementById('kpi-clean').closest('.kpi');

  if (currentMode === 'proto1') {
    const det = loadedData.filter(r => r.resultado === 'DRON DETECTADO').length;
    const limpio = n - det;
    document.getElementById('kpi-det').textContent = det > 0 ? det : (n > 0 ? det : '—');
    document.getElementById('kpi-det').className = 'kpi-val ' + (det > 0 ? 'red' : 'green');
    document.getElementById('kpi-det-label').textContent = 'DRON DETECTADO';
    document.getElementById('kpi-clean').textContent = n > 0 ? limpio : '—';
    document.getElementById('kpi-clean-label').textContent = 'SIN DRON';
    document.getElementById('kpi-extra').textContent = avgCos !== null ? avgCos : '—';
    document.getElementById('kpi-extra-label').textContent = 'COSENO PROM';
    document.getElementById('kpi-extra-sub').textContent = 'similitud media';
    if (detKpi)   detKpi.classList.toggle('kpi-glow-red',   det > 0);
    if (cleanKpi) cleanKpi.classList.toggle('kpi-glow-green', limpio > 0);
  }
}

// ── CHARTS ──
function buildCharts() {
  const cyanColor = '#00e5ff';
  const redColor = '#ff1744';
  const greenColor = '#00e676';
  const gridColor = 'rgba(0,229,255,.08)';
  const textColor = '#6b7a8d';

  // BAR CHART
  if (chartBar) chartBar.destroy();
  const barCtx = document.getElementById('chart-bar').getContext('2d');
  const barColors = loadedData.map(r =>
    r.resultado === 'DRON DETECTADO' ? redColor : greenColor
  );

  chartBar = new Chart(barCtx, {
    type: 'bar',
    data: {
      labels: loadedData.map((r, i) => (i + 1).toString()),
      datasets: [{
        label: 'Δ SNR (dB)',
        data: loadedData.map(r => r.delta_snr),
        backgroundColor: barColors.map(c => c + '88'),
        borderColor: barColors,
        borderWidth: 1
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: textColor, font: { size: 9 } }, grid: { color: gridColor } },
        y: { ticks: { color: textColor }, grid: { color: gridColor } }
      }
    }
  });

  // SCATTER
  if (chartScatter) chartScatter.destroy();
  const scatCtx = document.getElementById('chart-scatter').getContext('2d');
  const datasets = [];

  if (currentMode === 'proto1') {
    const dron = loadedData.filter(r => r.resultado === 'DRON DETECTADO');
    const safe = loadedData.filter(r => r.resultado !== 'DRON DETECTADO');
    if (safe.length) datasets.push({
      label: 'Sin Dron', data: safe.map(r => ({ x: r.delta_snr, y: r.coseno })),
      backgroundColor: greenColor + 'aa', borderColor: greenColor, pointRadius: 5
    });
    if (dron.length) datasets.push({
      label: 'Dron', data: dron.map(r => ({ x: r.delta_snr, y: r.coseno })),
      backgroundColor: redColor + 'aa', borderColor: redColor, pointRadius: 6
    });
  }

  const thresholdPlugin = {
    id: 'thresholds',
    afterDraw(chart) {
      const { ctx, chartArea, scales } = chart;
      if (!chartArea || !scales.x || !scales.y) return;
      ctx.save();
      ctx.setLineDash([5, 4]);
      ctx.strokeStyle = 'rgba(0,229,255,.35)';
      ctx.fillStyle   = 'rgba(0,229,255,.55)';
      ctx.lineWidth   = 1;
      ctx.font        = '9px JetBrains Mono, monospace';

      const xPx = scales.x.getPixelForValue(0.5);
      if (xPx >= chartArea.left && xPx <= chartArea.right) {
        ctx.beginPath(); ctx.moveTo(xPx, chartArea.top); ctx.lineTo(xPx, chartArea.bottom); ctx.stroke();
        ctx.textAlign = 'center';
        ctx.fillText('Umbral SNR', xPx, chartArea.top + 10);
      }

      const yPx = scales.y.getPixelForValue(0.7);
      if (yPx >= chartArea.top && yPx <= chartArea.bottom) {
        ctx.beginPath(); ctx.moveTo(chartArea.left, yPx); ctx.lineTo(chartArea.right, yPx); ctx.stroke();
        ctx.textAlign = 'left';
        ctx.fillText('Umbral Coseno', chartArea.left + 6, yPx - 5);
      }
      ctx.restore();
    }
  };

  const emptyScatterPlugin = {
    id: 'emptyScatter',
    afterDraw(chart) {
      if (chart.data.datasets.some(d => d.data && d.data.length > 0)) return;
      const { ctx, width, height } = chart;
      ctx.save();
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      const cx = width / 2, cy = height / 2 - 16;
      ctx.strokeStyle = 'rgba(0,229,255,.2)'; ctx.lineWidth = 1;
      [18, 10].forEach(r => { ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.stroke(); });
      ctx.strokeStyle = 'rgba(0,229,255,.4)';
      ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx, cy - 18); ctx.stroke();
      ctx.fillStyle = 'rgba(0,229,255,.45)';
      ctx.font = '11px JetBrains Mono, monospace';
      ctx.fillText('Sin capturas aún — inicia la detección para ver datos', cx, height / 2 + 18);
      ctx.restore();
    }
  };

  chartScatter = new Chart(scatCtx, {
    type: 'scatter',
    data: { datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: textColor, font: { size: 11 } } }
      },
      scales: {
        x: { title: { display: true, text: 'Δ SNR (dB)', color: textColor },
          ticks: { color: textColor }, grid: { color: gridColor } },
        y: { title: { display: true, text: 'Similitud Coseno', color: textColor },
          ticks: { color: textColor }, grid: { color: gridColor } }
      }
    },
    plugins: [thresholdPlugin, emptyScatterPlugin]
  });
}

// ── TABLE ──
function buildTable() {
  const tbody = document.getElementById('log-body');
  const count = document.getElementById('log-count');
  count.textContent = loadedData.length + ' registros';
  tbody.innerHTML = '';

  loadedData.forEach(r => {
    const tr = document.createElement('tr');
    let badgeClass, badgeText;

    if (currentMode === 'proto1') {
      if (r.resultado === 'DRON DETECTADO') {
        badgeClass = 'badge-danger'; badgeText = 'DRON DETECTADO';
      } else {
        badgeClass = 'badge-clear'; badgeText = 'SIN DRON';
      }
      const cellClass = badgeClass === 'badge-danger' ? 'td-danger' : 'td-clear';
      tr.innerHTML = `
        <td>${r.archivo}</td>
        <td class="${cellClass}"><span class="badge ${badgeClass}">${badgeText}</span></td>
        <td>${r.delta_snr >= 0 ? '+' : ''}${r.delta_snr.toFixed(4)}</td>
        <td>${r.coseno.toFixed(4)}</td>
        <td>${r.confianza}%</td>
        <td>${r.timestamp}</td>`;
    }
    tbody.appendChild(tr);
  });

  // Update table headers
  const thead = document.getElementById('log-thead');
  thead.innerHTML = '<tr><th>Archivo</th><th>Resultado</th><th>Δ SNR</th><th>Coseno</th><th>Confianza</th><th>Timestamp</th></tr>';
}

// ── CONFIRM MODAL ──
function showConfirm() {
  return new Promise(resolve => {
    const overlay = document.getElementById('confirm-overlay');
    overlay.style.display = 'flex';
    function done(val) {
      overlay.style.display = 'none';
      document.getElementById('confirm-ok').removeEventListener('click', onOk);
      document.getElementById('confirm-cancel').removeEventListener('click', onCancel);
      overlay.removeEventListener('click', onBg);
      resolve(val);
    }
    const onOk     = () => done(true);
    const onCancel = () => done(false);
    const onBg     = (e) => { if (e.target === overlay) done(false); };
    document.getElementById('confirm-ok').addEventListener('click', onOk);
    document.getElementById('confirm-cancel').addEventListener('click', onCancel);
    overlay.addEventListener('click', onBg);
  });
}

// ── CLEAR CAPTURES ──
async function clearCaptures() {
  const confirmed = await showConfirm();
  if (!confirmed) return;
  try {
    const res = await fetch('/api/clear-captures', { method: 'POST' });
    if (!res.ok) throw new Error('Error al eliminar capturas del servidor');
    const data = await res.json();
    if (data.status === 'success') {
      loadedData = [];
      lastFileHash = '';
      if (isLive) {
        // Mantener el dashboard activo — solo limpiar los datos mostrados
        resetAlert();
        updateKPIs();
        buildCharts();
        buildTable();
      } else {
        document.getElementById('dashboard').classList.add('hidden');
        document.getElementById('load-section').classList.remove('hidden');
        resetAlert();
        updateLiveToggleUI();
        startMetricsPolling();
      }
    } else {
      alert('Error: ' + data.message);
    }
  } catch (err) {
    alert('No se pudo conectar con el servidor para eliminar las capturas: ' + err.message);
  }
}

// ── SPLASH METRICS ──
let metricsPollInterval = null;

async function pollMetrics() {
  try {
    const res = await fetch('/api/stats');
    if (!res.ok) return;
    const d = await res.json();
    const capEl  = document.getElementById('sk-capturas');
    const detEl  = document.getElementById('sk-alertas');
    const uptEl  = document.getElementById('sk-uptime');
    if (capEl) capEl.textContent = d.capturas ?? '—';
    if (detEl) {
      detEl.textContent = d.alertas ?? '—';
      detEl.className = 'splash-kpi-val' + (d.alertas > 0 ? ' red' : '');
    }
    if (uptEl) uptEl.textContent = d.uptime ?? '—';
  } catch { /* servidor no responde */ }
}

function startMetricsPolling() {
  pollMetrics();
  if (!metricsPollInterval) metricsPollInterval = setInterval(pollMetrics, 2000);
}

function stopMetricsPolling() {
  if (metricsPollInterval) { clearInterval(metricsPollInterval); metricsPollInterval = null; }
}

// ── BOOT SCREEN ──
function initBootScreen() {
  const screen = document.getElementById('boot-screen');
  const clEl   = document.getElementById('boot-checklist');
  if (!screen || !clEl) return;

  document.getElementById('boot-skip').addEventListener('click', () => dismissBootScreen(true));

  const ITEMS = [
    { text: 'Inicializando PlutoSDR...',         badge: ' ', init: true   },
    { text: 'PlutoSDR conectado — 433.5 MHz',     badge: '✓'              },
    { text: 'Módulo de detección RF cargado',     badge: '✓'              },
    { text: 'Analizador espectral listo',          badge: '✓'              },
    { text: 'Verificando enlace MAVLink COM8...', badge: '~', mavlink: true },
  ];

  const els = ITEMS.map(item => {
    const row   = document.createElement('div');
    row.className = 'boot-check-item';
    const badge = document.createElement('span');
    badge.className = 'boot-check-badge';
    badge.textContent = `[${item.badge}]`;
    if (item.badge === '✓') badge.classList.add('ok');
    if (item.badge === '~') badge.classList.add('wait');
    const text = document.createElement('span');
    text.className = 'boot-check-text' + (!item.init && !item.mavlink ? ' done' : '');
    text.textContent = item.text;
    row.append(badge, text);
    clEl.appendChild(row);
    return { row, badge, text };
  });

  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  if (reducedMotion) {
    els.forEach(e => {
      e.row.classList.add('visible');
      if (e.badge.textContent === '[ ]') { e.badge.textContent = '[✓]'; e.badge.classList.add('ok'); }
    });
    setTimeout(() => dismissBootScreen(false), 1000);
    return;
  }

  const T0 = 800, DT = 280, T_PHASE4 = 2800, T_DISMISS = 3200;

  // Item 0: empty brackets → tick after 200ms
  setTimeout(() => {
    els[0].row.classList.add('visible');
    setTimeout(() => {
      els[0].badge.textContent = '[✓]';
      els[0].badge.className   = 'boot-check-badge ok';
      els[0].text.classList.add('done');
    }, 200);
  }, T0);

  // Items 1-3: appear with ✓ already
  for (let i = 1; i <= 3; i++) {
    setTimeout(() => els[i].row.classList.add('visible'), T0 + i * DT);
  }

  // Item 4 (MAVLink ~): appears and keeps blinking until phase 4
  setTimeout(() => els[4].row.classList.add('visible'), T0 + 4 * DT);

  // Phase 4: resolve MAVLink status, fade logo + list, fill progress bar
  setTimeout(() => {
    if (bootMavlinkStatus === 'connected') {
      els[4].badge.textContent = '[✓]';
      els[4].badge.className   = 'boot-check-badge ok';
      els[4].text.textContent  = 'MAVLink HEARTBEAT confirmado — COM8';
      els[4].text.classList.add('done');
    }
    // If still pending → keep [~] blink (radio may need more time)

    const logoWrap = screen.querySelector('.boot-logo-wrap');
    if (logoWrap) { logoWrap.style.transition = 'opacity .28s'; logoWrap.style.opacity = '0'; }
    clEl.style.transition = 'opacity .28s';
    clEl.style.opacity    = '0';

    const outer = document.getElementById('boot-progress-outer');
    const inner = document.getElementById('boot-progress-inner');
    outer.classList.add('visible');
    // Double rAF ensures the transition fires after the element is painted visible
    requestAnimationFrame(() => requestAnimationFrame(() => { inner.style.width = '100%'; }));
  }, T_PHASE4);

  // Phase 5: fade out entire boot screen (cross-dissolve with main app)
  setTimeout(() => dismissBootScreen(false), T_DISMISS);
}

function dismissBootScreen(immediate) {
  const screen = document.getElementById('boot-screen');
  if (!screen || screen.classList.contains('boot-hidden')) return;
  if (immediate) {
    screen.classList.add('boot-hidden');
    return;
  }
  screen.classList.add('boot-fading');
  const fallback = setTimeout(() => screen.classList.add('boot-hidden'), 600);
  screen.addEventListener('transitionend', () => {
    clearTimeout(fallback);
    screen.classList.add('boot-hidden');
  }, { once: true });
}

// ── INIT ──
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('btn-proto1').addEventListener('click', () => switchMode('proto1'));

  updateLiveToggleUI();

  // Clear button setup
  document.getElementById('btn-clear-captures').addEventListener('click', clearCaptures);

  // Hero CTA (splash) — start detection
  document.getElementById('btn-live-hero').addEventListener('click', toggleLive);
  // Stop button (header, only visible when live) — stop detection
  document.getElementById('btn-live-stop').addEventListener('click', toggleLive);

  // Clock
  setInterval(() => {
    document.getElementById('clock').textContent = new Date().toLocaleTimeString('es-MX', { hour12: false });
  }, 1000);

  // Boot screen starts immediately; main app initializes in parallel behind it
  initBootScreen();

  // Initialize and check background detection status
  switchMode('proto1');
  checkInitialDetectionStatus();
  startMetricsPolling();
});

