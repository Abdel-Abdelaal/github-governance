"use strict";
const byId = id => document.getElementById(id);
const number = value => new Intl.NumberFormat("en-GB", {maximumFractionDigits: 1}).format(value);
const sum = (rows, key) => rows.reduce((total, row) => total + (row[key] || 0), 0);
const tokenCount = (row, key) => row.usage_totals["gen_ai.usage." + key + "_tokens"];
let data;

function cell(row, text) {
  const item = document.createElement("td");
  item.textContent = text;
  row.append(item);
  return item;
}

function render() {
  const rows = data.rows.filter(row => (!byId("repository").value || row.repository === byId("repository").value) && (!byId("mode").value || row.mode === byId("mode").value));
  const attempted = sum(rows, "attempted_runs");
  const measured = sum(rows, "runs_with_usage");
  byId("runs").textContent = number(sum(rows, "records"));
  byId("coverage").textContent = number(attempted) + " attempted · " + number(sum(rows, "skipped")) + " skipped";
  byId("success").textContent = attempted ? number(100 * sum(rows, "valid_runs") / attempted) + "%" : "—";
  byId("tokens").textContent = measured ? number(rows.reduce((total, row) => total + (tokenCount(row, "input") || 0) + (tokenCount(row, "output") || 0), 0)) : "—";
  byId("usage-coverage").textContent = number(measured) + " / " + number(attempted) + " runs reported usage";
  byId("latency").textContent = attempted ? number(rows.reduce((total, row) => total + (row.mean_run_seconds || 0) * row.attempted_runs, 0) / attempted) + "s" : "—";
  byId("fleet").replaceChildren();
  byId("empty").hidden = rows.length !== 0;
  const failures = {};
  for (const item of rows) {
    const row = document.createElement("tr");
    const name = cell(row, item.repository.split("/").pop());
    const mode = document.createElement("small");
    mode.textContent = item.mode;
    name.append(mode);
    cell(row, number(item.attempted_runs) + " / " + number(item.skipped));
    cell(row, item.success_rate === null ? "—" : number(item.success_rate * 100) + "%");
    cell(row, item.attempts_per_run === null ? "—" : number(item.attempts_per_run));
    for (const key of ["input", "output", "reasoning.output"]) cell(row, tokenCount(item, key) === undefined ? "—" : number(tokenCount(item, key)));
    cell(row, number(item.findings_proposed) + " / " + number(item.findings_published));
    cell(row, number(item.patches.accepted || 0) + " / " + number(item.patches.rejected || 0));
    byId("fleet").append(row);
    for (const [key, count] of Object.entries(item.failure_categories)) failures[key] = (failures[key] || 0) + count;
  }
  byId("failures").replaceChildren();
  const ordered = Object.entries(failures).sort((a, b) => b[1] - a[1]);
  if (!ordered.length) byId("failures").textContent = rows.length ? "No recorded attempt failures in this selection." : "Awaiting verified records.";
  for (const [key, count] of ordered) {
    const row = document.createElement("div"); row.className = "bar-row";
    const label = document.createElement("div"); label.className = "bar-label";
    const name = document.createElement("span"); name.textContent = key;
    const total = document.createElement("span"); total.textContent = number(count);
    label.append(name, total);
    const track = document.createElement("div"); track.className = "bar-track";
    const fill = document.createElement("div"); fill.className = "bar-fill";
    fill.style.width = (100 * count / ordered[0][1]) + "%";
    track.append(fill); row.append(label, track); byId("failures").append(row);
  }
}

async function load() {
  try {
    const response = await fetch("metrics.json", {cache: "no-cache"});
    if (!response.ok) throw new Error("Metrics unavailable");
    data = await response.json();
    byId("updated").textContent = "Last collected " + new Date(data.generated_at).toLocaleString("en-GB", {timeZone: "UTC"}) + " UTC · signatures checked before aggregation";
    for (const [id, key] of [["repository", "repository"], ["mode", "mode"]]) {
      for (const value of [...new Set(data.rows.map(row => row[key]))].sort()) {
        const option = document.createElement("option"); option.value = value; option.textContent = value;
        byId(id).append(option);
      }
      byId(id).addEventListener("change", render);
    }
    if (data.collection_errors.length) {
      byId("notice").hidden = false;
      byId("notice").textContent = data.collection_errors.length + " collection or signature errors. Coverage is incomplete; inspect metrics.json and the collection run.";
    }
    render();
  } catch {
    byId("updated").textContent = "Metrics could not be loaded.";
    byId("notice").hidden = false;
    byId("notice").textContent = "The evidence dataset is unavailable. No measurements are being displayed.";
  }
}
load();
