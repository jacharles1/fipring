const charts = {};
let days = 30;
let daily = [];

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

function baseOptions() {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    interaction: { mode: "index", intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: { backgroundColor: css("--surface-1"), titleColor: css("--text-primary"),
                 bodyColor: css("--text-secondary"), borderColor: css("--border"), borderWidth: 1 },
    },
    scales: {
      x: { type: "time", time: { unit: days > 120 ? "month" : "day", tooltipFormat: "PP" },
           grid: { display: false }, border: { color: css("--baseline") },
           ticks: { color: css("--muted"), maxTicksLimit: 8, maxRotation: 0 } },
      y: { grid: { color: css("--grid") }, border: { display: false }, ticks: { color: css("--muted") } },
    },
  };
}

function line(label, key, color = "--series-1") {
  return {
    label, borderColor: css(color), backgroundColor: css(color), borderWidth: 2,
    pointRadius: 0, pointHoverRadius: 4, spanGaps: true, tension: 0.25,
    data: daily.filter((d) => d[key] != null).map((d) => ({ x: d.day, y: d[key] })),
  };
}

function bar(label, key, color = "--series-1") {
  return {
    // the 1px surface gap only reads when bars are wide enough; on dense ranges it swallows them
    label, backgroundColor: css(color), borderColor: css("--surface-1"),
    borderWidth: daily.length * 6 < innerWidth ? 1 : 0,
    borderRadius: 2, maxBarThickness: 18,
    data: daily.map((d) => ({ x: d.day, y: d[key] ?? null })),
  };
}

function draw(id, type, datasets, opts = baseOptions()) {
  charts[id]?.destroy();
  charts[id] = new Chart(document.getElementById(id), { type, data: { datasets }, options: opts });
}

function renderTiles(status) {
  const last = (key) => [...daily].reverse().find((d) => d[key] != null)?.[key];
  const dev = status.devices[0];
  const synced = dev?.last_sync_at ? new Date(dev.last_sync_at).toLocaleString([], { dateStyle: "short", timeStyle: "short" }) : null;
  const tiles = [
    ["Sleep", last("sleep_h"), "h"],
    ["HRV (RMSSD)", last("avg_rmssd"), "ms"],
    ["Lowest HR", last("lowest_hr"), "bpm"],
    ["Skin temp", last("skin_temp_c"), "°C"],
    ["Battery", dev?.battery, "%"],
    ["Last sync", synced, ""],
  ];
  document.getElementById("tiles").innerHTML = tiles.map(([label, v, unit]) =>
    `<div class="tile"><div class="label">${label}</div>
     <div class="value${label === "Last sync" ? " small" : ""}">${esc(v ?? "–")}<span class="unit">${v != null ? unit : ""}</span></div></div>`).join("");
}

function renderTable() {
  const cols = ["day", "sleep_h", "avg_rmssd", "lowest_hr", "avg_night_hr", "skin_temp_c", "avg_met"];
  document.getElementById("table").innerHTML =
    `<thead><tr>${cols.map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>` +
    [...daily].reverse().map((d) => `<tr>${cols.map((c) => `<td>${esc(d[c])}</td>`).join("")}</tr>`).join("") +
    "</tbody>";
}

function renderDaily() {
  renderTable();
  draw("sleep", "bar", [bar("Sleep", "sleep_h")]);
  draw("hrv", "line", [line("RMSSD", "avg_rmssd")]);
  draw("rhr", "line", [line("Lowest HR", "lowest_hr")]);
  draw("temp", "line", [line("Skin temp", "skin_temp_c")]);
  draw("met", "bar", [bar("Avg MET", "avg_met")]);
}

async function renderHeartRate() {
  const day = document.getElementById("hr-day").value;
  if (!day) return;
  const pts = await (await fetch(`/api/heartrate?day=${day}`)).json();
  const opts = baseOptions();
  opts.scales.x.time.unit = "hour";
  opts.scales.x.time.tooltipFormat = "p";
  const series = [["Overnight (hrv_event)", "hrv_event", "--series-1"], ["Daytime (green IBI)", "green_ibi", "--series-2"]];
  document.getElementById("legend-hr").innerHTML = series
    .map(([label, , color]) => `<span style="--c: var(${color})">${label}</span>`).join("");
  draw("hr", "line", series.map(([label, source, color]) => ({
    label, borderColor: css(color), backgroundColor: css(color), borderWidth: 2,
    pointRadius: 0, pointHoverRadius: 4, spanGaps: false,
    data: pts.filter((p) => p.source === source).map((p) => ({ x: p.t, y: p.bpm })),
  })), opts);
}

async function renderEventTypes() {
  const rows = await (await fetch("/api/event-types")).json();
  document.getElementById("types").innerHTML =
    "<thead><tr><th>event</th><th>tag</th><th>count</th><th>decoded</th><th>last seen</th></tr></thead><tbody>" +
    rows.map((r) => `<tr><td>${esc(r.name)}</td><td>${esc(r.tag)}</td><td>${r.count.toLocaleString()}</td>
      <td>${r.count ? Math.round((100 * r.decoded) / r.count) : 0}%</td>
      <td>${r.last_seen ? esc(new Date(r.last_seen).toLocaleString()) : "undated"}</td></tr>`).join("") +
    "</tbody>";
}

async function load() {
  const [status, d] = await Promise.all([
    fetch("/api/status").then((r) => r.json()),
    fetch(`/api/daily?days=${days}`).then((r) => r.json()),
  ]);
  daily = d;
  document.getElementById("status").textContent = status.devices.length
    ? `${status.events.toLocaleString()} events stored from ring ${status.devices.map((x) => x.serial).join(", ")}` +
      (status.undated_events ? ` (${status.undated_events.toLocaleString()} not yet dated)` : "")
    : "No ring synced yet. Run `python -m ring.cli sync` on the collector.";
  renderTiles(status);
  renderDaily();
  const hrDay = document.getElementById("hr-day");
  if (!hrDay.value && daily.length) hrDay.value = daily[daily.length - 1].day;
  renderHeartRate();
  renderEventTypes();
}

document.querySelectorAll(".range button").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll(".range button").forEach((o) => o.setAttribute("aria-pressed", o === b));
  days = Number(b.dataset.days);
  load();
}));
document.getElementById("hr-day").addEventListener("change", renderHeartRate);
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { renderDaily(); renderHeartRate(); });

load();
