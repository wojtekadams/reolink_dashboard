"use strict";

const grid = document.getElementById("grid");
const emptyEl = document.getElementById("empty");
const updatedEl = document.getElementById("updated");
const onlineCountEl = document.getElementById("online-count");
const modal = document.getElementById("modal");

let chart = null;
let currentChannel = null;
let currentHours = 24;

/* ---------- helpers ---------- */
function stateOf(cam) {
  if (cam.offline) return "offline";
  if (cam.sleep) return "sleep";
  return "online";
}
function stateLabel(s) {
  return { online: "ONLINE", sleep: "SLEEP", offline: "OFFLINE" }[s];
}
function barClass(p) {
  if (p == null) return "lo";
  if (p >= 50) return "hi";
  if (p >= 20) return "mid";
  return "lo";
}
function fmtTime(ts) {
  return new Date(ts * 1000).toLocaleString("pl-PL", {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

/* ---------- matrix ---------- */
async function loadCameras() {
  try {
    const r = await fetch("/api/cameras");
    const data = await r.json();
    renderGrid(data.cameras);
    updatedEl.textContent = data.updated
      ? "last reading " + fmtTime(data.updated)
      : "no data";
    const online = data.cameras.filter((c) => !c.offline).length;
    onlineCountEl.textContent = `${online} / ${data.cameras.length} online`;
  } catch (e) {
    updatedEl.textContent = "API connection error";
  }
}

function renderGrid(cameras) {
  if (!cameras.length) {
    emptyEl.style.display = "block";
    grid.innerHTML = "";
    return;
  }
  emptyEl.style.display = "none";
  grid.innerHTML = "";

  for (const cam of cameras) {
    const st = stateOf(cam);
    const p = cam.battery ? cam.battery.percent : null;
    const charging = cam.battery && cam.battery.charging === 1;
    const stale = cam.battery && cam.battery.stale;

    const tile = document.createElement("div");
    tile.className = "tile";
    tile.onclick = () => openDetail(cam.channel);

    tile.innerHTML = `
      <div class="thumb">
        ${
          cam.offline
            ? `<div class="thumb-fallback">offline</div>`
            : `<img loading="lazy" alt="" src="/api/camera/${cam.channel}/snapshot"
                    onload="this.classList.add('ok')"
                    onerror="this.parentNode.classList.add('noimg')">
               <div class="thumb-fallback">no frame</div>`
        }
        <span class="ch-chip">CH${cam.channel}</span>
      </div>
      <div class="tile-body">
        <div class="tile-head">
          <span class="dot ${st}"></span>
          <span class="tile-name">${cam.name}</span>
        </div>
        ${
          p != null
            ? `<div class="batt-big">${p}<span class="unit">%</span></div>
               <div class="bar"><span class="${barClass(p)}" style="width:${p}%"></span></div>`
            : `<div class="batt-big na">— no data —</div>
               <div class="bar"><span class="lo" style="width:0%"></span></div>`
        }
        <div class="tile-foot">
          <span class="state-mini">${stateLabel(st)}</span>
          <span>${charging ? '<span class="charging">⚡ charging</span>' : (stale ? '<span class="stale">⟳ stale reading</span>' : "")}</span>
        </div>
      </div>`;
    grid.appendChild(tile);
  }
}

/* ---------- detail modal ---------- */
let player = null;

async function openDetail(channel) {
  currentChannel = channel;
  modal.classList.remove("hidden");
  setText("d-name", "…");

  const r = await fetch(`/api/camera/${channel}`);
  if (!r.ok) return;
  const cam = await r.json();
  const st = stateOf(cam);
  const b = cam.battery || {};

  setText("d-name", cam.name);
  const tag = document.getElementById("d-state");
  tag.textContent = stateLabel(st);
  tag.className = "state-tag " + st;

  setText("d-battery", b.percent != null ? b.percent + " %" : "—");
  setText("d-charge", b.charging === 1 ? "⚡ charging" : b.charging === 0 ? "discharging" : (b.charge_status || "—"));
  setText("d-voltage", b.voltage_v != null ? b.voltage_v + " V" : "—");
  setText("d-temp", b.temperature != null ? b.temperature + " °C" : "—");
  setText("d-lowpower", b.low_power ? "TAK" : "nie");
  setText("d-uid", cam.uid || "—");
  setText("d-channel", "CH" + cam.channel);

  startStream(channel, cam.offline);
  loadHistory(channel, currentHours);
}

function startStream(channel, offline) {
  const video = document.getElementById("d-video");
  const fb = document.getElementById("snap-fallback");
  const badge = document.getElementById("live-badge");
  stopStream();
  video.style.display = "none";
  badge.style.display = "none";

  if (offline) { fb.textContent = "camera offline"; fb.style.display = "flex"; return; }
  if (!window.mpegts || !mpegts.isSupported()) {
    fb.textContent = "browser does not support live preview"; fb.style.display = "flex"; return;
  }

  fb.textContent = "connecting to stream…";
  fb.style.display = "flex";

  player = mpegts.createPlayer(
    { type: "flv", isLive: true, url: `/api/camera/${channel}/stream` },
    { enableStashBuffer: false, liveBufferLatencyChasing: true, lazyLoad: false }
  );
  player.attachMediaElement(video);
  player.on(mpegts.Events.ERROR, () => {
    fb.textContent = "no preview (camera may be sleeping)";
    fb.style.display = "flex"; video.style.display = "none"; badge.style.display = "none";
  });
  video.onplaying = () => { video.style.display = "block"; fb.style.display = "none"; badge.style.display = "inline-flex"; };
  try { player.load(); player.play().catch(() => {}); } catch (e) {}
}

function stopStream() {
  if (player) {
    try { player.pause(); player.unload(); player.detachMediaElement(); player.destroy(); }
    catch (e) {}
    player = null;
  }
  const video = document.getElementById("d-video");
  if (video) { video.onplaying = null; }
}

function closeModal() {
  stopStream();
  modal.classList.add("hidden");
  currentChannel = null;
}
document.getElementById("modal-close").onclick = closeModal;
modal.onclick = (e) => { if (e.target === modal) closeModal(); };
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

document.querySelectorAll(".range-buttons button").forEach((btn) => {
  btn.onclick = () => {
    document.querySelectorAll(".range-buttons button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    currentHours = parseInt(btn.dataset.h, 10);
    if (currentChannel != null) loadHistory(currentChannel, currentHours);
  };
});

function setText(id, val) { document.getElementById(id).textContent = val; }

/* ---------- powiekszanie podgladu (pelny ekran) ---------- */
const expandBtn = document.getElementById("d-expand");
function toggleVideoFullscreen() {
  const wrap = document.querySelector(".snap-wrap");
  const fsEl = document.fullscreenElement || document.webkitFullscreenElement;
  if (fsEl) {
    (document.exitFullscreen || document.webkitExitFullscreen).call(document);
  } else {
    (wrap.requestFullscreen || wrap.webkitRequestFullscreen || (() => {})).call(wrap);
  }
}
expandBtn.onclick = toggleVideoFullscreen;
function onFsChange() {
  const video = document.getElementById("d-video");
  const fsEl = document.fullscreenElement || document.webkitFullscreenElement;
  if (fsEl) { video.setAttribute("controls", ""); expandBtn.textContent = "⤡"; }
  else { video.removeAttribute("controls"); expandBtn.textContent = "⤢"; }
}
document.addEventListener("fullscreenchange", onFsChange);
document.addEventListener("webkitfullscreenchange", onFsChange);

/* ---------- chart ---------- */
async function loadHistory(channel, hours) {
  const r = await fetch(`/api/camera/${channel}/history?hours=${hours}`);
  const data = await r.json();
  renderChart(data.points);
}

// Plugin: shade contiguous spans where charging === 1.
const chargingBands = {
  id: "chargingBands",
  beforeDatasetsDraw(c) {
    const flags = c.$chargingFlags || [];
    const { ctx, chartArea, scales } = c;
    if (!flags.length) return;
    ctx.save();
    ctx.fillStyle = "rgba(14,165,233,0.12)";
    let start = null;
    for (let i = 0; i < flags.length; i++) {
      if (flags[i] === 1 && start === null) start = i;
      const ended = flags[i] !== 1 || i === flags.length - 1;
      if (start !== null && ended) {
        const end = flags[i] === 1 ? i : i - 1;
        const x1 = scales.x.getPixelForValue(start);
        const x2 = scales.x.getPixelForValue(Math.max(end, start));
        const w = Math.max(x2 - x1, 3);
        ctx.fillRect(x1, chartArea.top, w, chartArea.bottom - chartArea.top);
        start = null;
      }
    }
    ctx.restore();
  },
};

function renderChart(points) {
  const ctx = document.getElementById("d-chart");
  const labels = points.map((p) => fmtTime(p.ts));
  const percent = points.map((p) => p.percent);
  const flags = points.map((p) => p.charging);

  if (chart) chart.destroy();
  chart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [{
        label: "Battery %",
        data: percent,
        borderColor: "#16a34a",
        backgroundColor: "rgba(34,197,94,0.12)",
        borderWidth: 2.5,
        pointRadius: 0,
        tension: 0.25,
        fill: true,
        spanGaps: true,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        y: { min: 0, max: 100, grid: { color: "#eaeef5" },
             ticks: { color: "#7a869a", callback: (v) => v + "%" } },
        x: { grid: { display: false },
             ticks: { color: "#7a869a", maxTicksLimit: 8, autoSkip: true } },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            afterLabel: (item) =>
              flags[item.dataIndex] === 1 ? "⚡ charging" : "discharging",
          },
        },
      },
    },
    plugins: [chargingBands],
  });
  chart.$chargingFlags = flags;
  chart.update();
}

/* ---------- storage (HDD pie / donut per disk) ---------- */
function gb(mb) { return (mb / 1024).toFixed(1); }

function donutSvg(pct) {
  const R = 52, C = 2 * Math.PI * R;
  const off = C * (1 - pct / 100);
  const col = pct >= 90 ? "var(--offline)" : pct >= 70 ? "var(--sleep)" : "var(--online-2)";
  return `<svg viewBox="0 0 130 130" class="donut">
    <circle cx="65" cy="65" r="${R}" fill="none" stroke="#e7ecf4" stroke-width="14"/>
    <circle cx="65" cy="65" r="${R}" fill="none" stroke="${col}" stroke-width="14"
      stroke-linecap="round" stroke-dasharray="${C.toFixed(1)}" stroke-dashoffset="${off.toFixed(1)}"
      transform="rotate(-90 65 65)"/>
    <text x="65" y="61" text-anchor="middle" class="donut-pct">${pct}%</text>
    <text x="65" y="80" text-anchor="middle" class="donut-sub">used</text>
  </svg>`;
}

async function loadHdd() {
  let disks = [];
  try {
    const r = await fetch("/api/hdd");
    disks = await r.json();
  } catch (e) { /* ignore */ }

  const wrap = document.getElementById("disks");
  const section = document.getElementById("storage");
  if (!disks.length) { section.classList.add("hidden"); return; }
  section.classList.remove("hidden");

  wrap.innerHTML = disks.map((d) => `
    <div class="disk">
      ${donutSvg(d.used_pct)}
      <div class="disk-info">
        <b>Disk ${d.number != null ? d.number : "?"}</b>
        <span>${gb(d.used_mb)} / ${gb(d.capacity_mb)} GB</span>
        <span class="disk-free">${gb(d.free_mb)} GB free</span>
      </div>
    </div>`).join("");
}

/* ---------- boot ---------- */
loadCameras();
loadHdd();
setInterval(() => { if (modal.classList.contains("hidden")) loadCameras(); }, 30000);
setInterval(loadHdd, 300000);
