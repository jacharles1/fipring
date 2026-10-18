const charts = {};
let days = 90;
let daily = [];

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function baseOptions(extra = {}) {
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
    ...extra,
  };
}

function line(label, key, color) {
  return {
    label, borderColor: css(color), backgroundColor: css(color), borderWidth: 2,
    pointRadius: 0, pointHoverRadius: 4, spanGaps: true, tension: 0.25,
    data: daily.filter((d) => d[key] != null).map((d) => ({ x: d.day, y: d[key] })),
  };
}

function bar(label, key, color) {
  return {
    // the 1px surface gap only reads when bars are wide enough; on dense ranges it swallows them
    label, backgroundColor: css(color), borderColor: css("--surface-1"),
    borderWidth: daily.length * 6 < innerWidth ? 1 : 0,
    borderRadius: 2, maxBarThickness: 18,
    data: daily.map((d) => ({ x: d.day, y: d[key] ?? null })),
  };
}

function draw(id, config) {
  charts[id]?.destroy();
  charts[id] = new Chart(document.getElementById(id), config);
}

function legend(id, items) {
  document.getElementById(id).innerHTML = items
    .map(([label, color]) => `<span style="--c: var(${color})">${label}</span>`).join("");
}

function renderTiles() {
  const last = (key) => [...daily].reverse().find((d) => d[key] != null)?.[key];
  const tiles = [
    ["Readiness", last("readiness"), ""],
    ["Sleep score", last("sleep_score"), ""],
    ["Activity score", last("activity_score"), ""],
    ["HRV", last("avg_hrv"), "ms"],
    ["Lowest HR", last("lowest_hr"), "bpm"],
    ["Steps", last("steps")?.toLocaleString(), ""],
  ];
  document.getElementById("tiles").innerHTML = tiles.map(([label, v, unit]) =>
    `<div class="tile"><div class="label">${label}</div>
     <div class="value">${v ?? "–"}<span class="unit">${v != null ? unit : ""}</span></div></div>`).join("");
}

function renderTable() {
  const cols = ["day", "readiness", "sleep_score", "activity_score", "steps", "deep_h", "rem_h",
                "light_h", "awake_h", "avg_hrv", "lowest_hr", "temp_deviation"];
  document.getElementById("table").innerHTML =
    `<thead><tr>${cols.map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>` +
    [...daily].reverse().map((d) => `<tr>${cols.map((c) => `<td>${d[c] ?? ""}</td>`).join("")}</tr>`).join("") +
    "</tbody>";
}

function renderDaily() {
  renderTiles();
  renderTable();

  const scores = [["Readiness", "readiness", "--series-1"], ["Sleep", "sleep_score", "--series-2"],
                  ["Activity", "activity_score", "--series-3"]];
  legend("legend-scores", scores.map(([l, , c]) => [l, c]));
  draw("scores", { type: "line", data: { datasets: scores.map((s) => line(...s)) },
                   options: baseOptions() });

  const stages = [["Deep", "deep_h", "--series-1"], ["REM", "rem_h", "--series-2"],
                  ["Light", "light_h", "--series-3"], ["Awake", "awake_h", "--series-4"]];
  legend("legend-stages", stages.map(([l, , c]) => [l, c]));
  const stacked = baseOptions();
  stacked.scales.x.stacked = true;
  stacked.scales.y.stacked = true;
  draw("stages", { type: "bar", data: { datasets: stages.map((s) => bar(...s)) }, options: stacked });

  draw("hrv", { type: "line", data: { datasets: [line("HRV", "avg_hrv", "--series-1")] }, options: baseOptions() });
  draw("rhr", { type: "line", data: { datasets: [line("Lowest HR", "lowest_hr", "--series-1")] }, options: baseOptions() });
  draw("steps", { type: "bar", data: { datasets: [bar("Steps", "steps", "--series-1")] }, options: baseOptions() });
}

async function renderHeartRate() {
  const day = document.getElementById("hr-day").value;
  if (!day) return;
  const samples = await (await fetch(`/api/heartrate?day=${day}`)).json();
  const opts = baseOptions();
  opts.scales.x.time.unit = "hour";
  opts.scales.x.time.tooltipFormat = "p";
  draw("hr", { type: "line", data: { datasets: [{
    label: "BPM", borderColor: css("--series-1"), borderWidth: 2, pointRadius: 0, pointHoverRadius: 4,
    data: samples.map((s) => ({ x: s.t, y: s.bpm })),
  }] }, options: opts });
}

async function loadStatus() {
  const s = await (await fetch("/api/status")).json();
  document.getElementById("connect").hidden = s.connected;
  const total = Object.values(s.documents).reduce((a, b) => a + b, 0);
  document.getElementById("status").textContent = !s.connected
    ? "Not connected to Oura yet."
    : `${s.syncing ? "Syncing… " : ""}${total} documents, ${s.heartrate_samples} heart-rate samples, ${s.ble_packets} BLE packets stored.`;
  return s;
}

async function loadDaily() {
  daily = await (await fetch(`/api/daily?days=${days}`)).json();
  renderDaily();
  const hrDay = document.getElementById("hr-day");
  if (!hrDay.value && daily.length) hrDay.value = daily[daily.length - 1].day;
  renderHeartRate();
}

document.querySelectorAll(".range button").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll(".range button").forEach((o) => o.setAttribute("aria-pressed", o === b));
  days = Number(b.dataset.days);
  loadDaily();
}));
document.getElementById("hr-day").addEventListener("change", renderHeartRate);
document.getElementById("sync").addEventListener("click", async () => {
  await fetch("/api/sync", { method: "POST" });
  const poll = setInterval(async () => {
    const s = await loadStatus();
    if (!s.syncing) { clearInterval(poll); loadDaily(); }
  }, 3000);
});
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { renderDaily(); renderHeartRate(); });

loadStatus();
loadDaily();
