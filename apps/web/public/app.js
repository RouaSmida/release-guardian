// Release Guardian demo UI: plain JavaScript, no build step.
const PULSE_SIZE = 60;
const outcomes = [];
const $ = (id) => document.getElementById(id);

function record(ok) {
  outcomes.push(ok);
  if (outcomes.length > PULSE_SIZE) outcomes.shift();
  const pulse = $("pulse");
  pulse.replaceChildren(
    ...Array.from({ length: PULSE_SIZE }, (_, i) => {
      const cell = document.createElement("i");
      if (i < outcomes.length) cell.className = outcomes[i] ? "ok" : "fail";
      return cell;
    }),
  );
  const okCount = outcomes.filter(Boolean).length;
  $("ok-count").textContent = okCount;
  $("fail-count").textContent = outcomes.length - okCount;
  $("success-rate").textContent = `${((okCount / outcomes.length) * 100).toFixed(0)}%`;
}

async function getJson(url, options) {
  try {
    const response = await fetch(url, options);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    record(true);
    return data;
  } catch (error) {
    record(false);
    throw error;
  }
}

function badge(name, info) {
  const el = document.createElement("span");
  const faulty = info && info.fault_mode !== "none";
  el.className = `badge${faulty ? " fault" : ""}`;
  const dot = document.createElement("span");
  dot.className = `dot${info ? "" : " down"}`;
  const label = document.createElement("b");
  label.textContent = name;
  el.append(dot, label, info ? info.version : "unreachable");
  if (faulty) el.append(` · fault: ${info.fault_mode}`);
  return el;
}

function cell(text) {
  const td = document.createElement("td");
  td.textContent = text ?? "–";
  return td;
}

function renderJobs(jobs) {
  const body = $("jobs");
  if (!jobs.length) return;
  body.replaceChildren(
    ...jobs.map((job) => {
      const row = document.createElement("tr");
      const status = document.createElement("span");
      status.className = `status ${job.status}`;
      status.textContent = job.status;
      const statusCell = document.createElement("td");
      statusCell.append(status);
      row.append(
        cell(job.id),
        statusCell,
        cell(job.result?.words),
        cell(job.result?.unique_words),
        cell(job.processed_by),
        cell(new Date(job.created_at).toLocaleTimeString()),
      );
      return row;
    }),
  );
}

async function refresh() {
  const [web, api, jobs] = await Promise.allSettled([
    getJson("/info"),
    getJson("/api/info"),
    getJson("/api/jobs?limit=15"),
  ]);
  $("versions").replaceChildren(badge("web", web.value), badge("api", api.value));
  if (jobs.status === "fulfilled") renderJobs(jobs.value.jobs);
}

$("job-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.target.querySelector("button");
  const message = $("form-message");
  button.disabled = true;
  try {
    const job = await getJson("/api/jobs", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text: $("job-text").value }),
    });
    message.className = "";
    message.textContent = `Queued job ${job.id}`;
    $("job-text").value = "";
  } catch (error) {
    message.className = "error";
    message.textContent = `Request failed (${error.message})`;
  } finally {
    button.disabled = false;
  }
});

refresh();
setInterval(refresh, 2000);
