const form = document.querySelector("#upload-form");
const fileInput = document.querySelector("#email-file");
const fileLabel = document.querySelector("#file-label");
const startButton = document.querySelector("#start-button");
const errorMessage = document.querySelector("#error-message");
const progressPanel = document.querySelector("#progress-panel");
const progressTitle = document.querySelector("#progress-title");
const progressCopy = document.querySelector("#progress-copy");
const progressBar = document.querySelector("#progress-bar");
const progressMeta = document.querySelector("#progress-meta");
const statusChip = document.querySelector("#status-chip");
const reportSection = document.querySelector("#report-section");
const resultsList = document.querySelector("#results-list");
const filterSelect = document.querySelector("#filter-select");
const searchInput = document.querySelector("#search-input");
const warningsBox = document.querySelector("#warnings");
const modelSelect = document.querySelector("#model-select");
const modelFeedback = document.querySelector("#model-feedback");
const refreshModelsButton = document.querySelector("#refresh-models");
let currentReport = null;
let pollTimer = null;

const sectionNames = {
  deadline: "Deadlines to check",
  waiting: "Waiting on you",
  task: "You may have forgotten",
  decision: "Important decisions",
  revisit: "Worth revisiting",
};

fileInput.addEventListener("change", () => {
  fileLabel.textContent = fileInput.files[0]?.name || "Choose an email export";
});

const dropzone = document.querySelector("#dropzone");
dropzone.addEventListener("dragover", event => {
  event.preventDefault();
  dropzone.classList.add("dragging");
});
dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragging"));
dropzone.addEventListener("drop", event => {
  event.preventDefault();
  dropzone.classList.remove("dragging");
  const file = event.dataTransfer.files[0];
  if (!file) return;
  const extension = file.name.toLowerCase().split(".").pop();
  if (!["mbox", "eml", "zip"].includes(extension)) {
    showError("Please choose an .mbox, .eml, or ZIP file.");
    return;
  }
  const transfer = new DataTransfer();
  transfer.items.add(file);
  fileInput.files = transfer.files;
  fileLabel.textContent = file.name;
  hideError();
});

function showError(message) {
  errorMessage.className = "message error";
  errorMessage.textContent = message;
  errorMessage.hidden = false;
}
function hideError() {
  errorMessage.hidden = true;
  errorMessage.textContent = "";
}
function setProgress(status, done = 0, total = 0) {
  statusChip.textContent = status.toUpperCase();
  const percent = total > 0 ? Math.round((done / total) * 100) : 0;
  progressBar.style.width = `${percent}%`;
  progressMeta.textContent = total > 0
    ? `${done.toLocaleString()} of ${total.toLocaleString()} messages analyzed`
    : "Waiting for processing to start";
  if (status === "queued") {
    progressTitle.textContent = "Preparing your recovery brief";
    progressCopy.textContent = "Your emails are parsed locally. Waiting for the analysis pipeline…";
  } else if (status === "processing") {
    progressTitle.textContent = "Finding the signals in your inbox";
    progressCopy.textContent = "The local model is reviewing small batches of messages.";
  } else if (status === "completed") {
    progressTitle.textContent = "Recovery brief ready";
    progressCopy.textContent = "Review the findings and check their source excerpts before acting.";
  }
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  hideError();
  const file = fileInput.files[0];
  if (!file) return showError("Choose an .mbox, .eml, or ZIP file first.");
  beginProcessing();
  try {
    const data = new FormData();
    data.append("file", file);
    const response = await fetch("/api/recovery/start", { method: "POST", body: data });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Could not start recovery.");
    setProgress(result.status);
    progressCopy.textContent = `Imported ${result.emails_found.toLocaleString()} unique messages from ${result.filename}.`;
    await pollStatus(result.job_id);
  } catch (error) {
    showError(error.message || "Unexpected error while starting recovery.");
    endProcessing();
  }
});

function beginProcessing() {
  if (pollTimer) clearTimeout(pollTimer);
  reportSection.hidden = true;
  progressPanel.hidden = false;
  startButton.disabled = true;
  startButton.textContent = "Working…";
  demoButton.disabled = true;
}
function endProcessing() {
  startButton.disabled = false;
  startButton.innerHTML = 'Start recovery <span>→</span>';
  demoButton.disabled = false;
}

async function pollStatus(jobId) {
  try {
    const response = await fetch(`/api/recovery/status/${encodeURIComponent(jobId)}`);
    const status = await response.json();
    if (!response.ok) throw new Error(status.detail || "Could not read job status.");
    setProgress(status.status, status.progress_done, status.progress_total);
    if (status.status === "completed") {
      const reportResponse = await fetch(`/api/recovery/report/${encodeURIComponent(jobId)}`);
      const report = await reportResponse.json();
      if (!reportResponse.ok) throw new Error(report.detail || "Could not load report.");
      currentReport = report;
      renderReport(report);
      endProcessing();
      return;
    }
    if (status.status === "failed") throw new Error(status.error || "Recovery job failed.");
    pollTimer = setTimeout(() => pollStatus(jobId), 1200);
  } catch (error) {
    showError(error.message || "Could not check recovery status.");
    endProcessing();
  }
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[char]);
}

function renderReport(report) {
  reportSection.hidden = false;
  document.querySelector("#report-subtitle").textContent =
    `${report.filename} · ${report.emails_processed.toLocaleString()} messages analyzed`;
  document.querySelector("#stat-emails").textContent = report.emails_processed.toLocaleString();
  document.querySelector("#stat-actions").textContent = report.action_count.toLocaleString();
  document.querySelector("#stat-high").textContent = report.high_priority_count.toLocaleString();
  document.querySelector("#stat-threads").textContent = report.threads_found.toLocaleString();
  warningsBox.hidden = !report.warnings?.length;
  warningsBox.innerHTML = report.warnings?.length
    ? `<strong>Notes from this run</strong><ul>${report.warnings.map(w => `<li>${escapeHtml(w)}</li>`).join("")}</ul>`
    : "";
  renderItems();
  reportSection.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderItems() {
  if (!currentReport) return;
  const filter = filterSelect.value;
  const query = searchInput.value.trim().toLowerCase();
  const items = currentReport.items.filter(item => {
    if (filter !== "all" && item.section !== filter) return false;
    const searchable = [item.subject, item.sender, item.summary, item.evidence, item.action]
      .filter(Boolean).join(" ").toLowerCase();
    return !query || searchable.includes(query);
  });
  if (!items.length) {
    resultsList.innerHTML = '<div class="empty-state">Nothing in this brief matches. Try another filter or search.</div>';
    return;
  }

  const grouped = new Map();
  for (const item of items) {
    const section = sectionNames[item.section] || "Worth revisiting";
    if (!grouped.has(section)) grouped.set(section, []);
    grouped.get(section).push(item);
  }
  resultsList.innerHTML = [...grouped.entries()].map(([section, sectionItems]) => `
    <section class="finding-group">
      <h3>${escapeHtml(section)} <span>${sectionItems.length}</span></h3>
      <div class="finding-cards">${sectionItems.map(renderItem).join("")}</div>
    </section>
  `).join("");
}

function renderItem(item) {
  const badges = [
    `<span class="badge ${escapeHtml(item.priority)}">${escapeHtml(item.priority)} priority</span>`,
    item.confidence ? `<span class="badge">${escapeHtml(item.confidence)} confidence</span>` : "",
  ].join("");
  const actionText = item.action_required
    ? `<div class="action-box"><strong>Possible next step:</strong> ${escapeHtml(item.action || "Review whether this still needs attention.")}${item.deadline ? `<br><strong>Deadline mentioned:</strong> ${escapeHtml(item.deadline)}` : ""}</div>`
    : "";
  const evidence = item.evidence
    ? `<details class="evidence"><summary>View source excerpt</summary><blockquote>${escapeHtml(item.evidence)}</blockquote></details>`
    : "";
  return `<article class="result-card">
    <div class="result-top">
      <div>
        <h4>${escapeHtml(item.subject || "(no subject)")}</h4>
        <p class="result-meta">${escapeHtml(item.sender)}${item.date ? " · " + escapeHtml(item.date) : ""}</p>
      </div>
      <span class="badge source-id">Source ${escapeHtml(String(item.email_id).slice(0, 18))}${String(item.email_id).length > 18 ? "…" : ""}</span>
    </div>
    <div class="badges">${badges}</div>
    <p class="interpretation-label">Interpretation</p>
    <p class="result-summary">${escapeHtml(item.summary)}</p>
    ${actionText}
    <details class="evidence source-reference"><summary>Original message reference</summary><code>${escapeHtml(item.email_id)}</code></details>
    ${evidence}
  </article>`;
}

filterSelect.addEventListener("change", renderItems);
searchInput.addEventListener("input", renderItems);

const demoButton = document.querySelector("#demo-button");
demoButton.addEventListener("click", async () => {
  hideError();
  reportSection.hidden = true;
  progressPanel.hidden = true;
  demoButton.disabled = true;
  demoButton.textContent = "Loading demo…";
  try {
    const response = await fetch("/api/recovery/demo", { method: "POST" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Could not load demo.");
    const reportResponse = await fetch(`/api/recovery/report/${encodeURIComponent(result.job_id)}`);
    const report = await reportResponse.json();
    if (!reportResponse.ok) throw new Error(report.detail || "Could not load demo report.");
    currentReport = report;
    progressPanel.hidden = true;
    renderReport(report);
  } catch (error) {
    showError(error.message || "Could not load demo.");
  } finally {
    demoButton.disabled = false;
    demoButton.textContent = "Try synthetic demo";
  }
});

document.querySelector("#clear-button").addEventListener("click", async () => {
  if (!window.confirm("Delete all saved recovery reports and demo results from this app?")) return;
  hideError();
  try {
    const response = await fetch("/api/data", { method: "DELETE" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Could not clear local data.");
    currentReport = null;
    reportSection.hidden = true;
    progressPanel.hidden = true;
    showError("Saved reports and results were cleared from this app.");
    errorMessage.className = "message success";
  } catch (error) {
    showError(error.message || "Could not clear local data.");
  }
});

document.querySelector("#download-button").addEventListener("click", () => {
  if (!currentReport) return;
  const blob = new Blob([JSON.stringify(currentReport, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `mailstrata-${currentReport.job_id}.json`;
  link.click();
  URL.revokeObjectURL(url);
});

async function loadModelStatus() {
  const status = document.querySelector("#model-status");
  try {
    const response = await fetch("/api/health");
    const health = await response.json();
    if (!response.ok) throw new Error("Status unavailable");
    status.textContent = health.model_available
      ? `Ollama ready · ${health.model} is installed`
      : health.ollama_available
        ? `Ollama is running · selected model ${health.model} is not installed`
        : `Ollama not detected · demo still works`;
    status.classList.toggle("offline", !health.model_available);
  } catch {
    status.textContent = "Local model status unavailable";
    status.classList.add("offline");
  }
}

async function loadModels() {
  modelFeedback.textContent = "Loading installed models…";
  refreshModelsButton.disabled = true;
  modelSelect.disabled = true;
  try {
    const response = await fetch("/api/models");
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Could not load installed models.");
    modelSelect.replaceChildren();
    for (const model of data.models) {
      const option = document.createElement("option");
      option.value = model;
      option.textContent = model;
      modelSelect.append(option);
    }
    if (data.active_model && !data.models.includes(data.active_model)) {
      const missing = document.createElement("option");
      missing.value = data.active_model;
      missing.textContent = `${data.active_model} (not installed)`;
      modelSelect.append(missing);
    }
    modelSelect.value = data.active_model;
    modelSelect.disabled = data.models.length === 0;
    modelFeedback.textContent = data.models.length
      ? "Changes apply to the next recovery; in-progress jobs keep their current model."
      : "No Ollama models are installed yet.";
    if (data.models.length === 0) modelFeedback.classList.add("offline");
  } catch (error) {
    modelFeedback.textContent = error.message || "Could not load installed models.";
    modelFeedback.classList.add("offline");
  } finally {
    refreshModelsButton.disabled = false;
  }
}

modelSelect.addEventListener("change", async () => {
  modelSelect.disabled = true;
  modelFeedback.classList.remove("offline");
  modelFeedback.textContent = "Switching local model…";
  try {
    const response = await fetch("/api/config/model", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: modelSelect.value }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Could not change model.");
    modelFeedback.textContent = `Active model: ${result.active_model}. This choice is saved locally.`;
    await loadModelStatus();
  } catch (error) {
    modelFeedback.textContent = error.message || "Could not change model.";
    modelFeedback.classList.add("offline");
    await loadModels();
  } finally {
    modelSelect.disabled = false;
  }
});

refreshModelsButton.addEventListener("click", loadModels);

loadModelStatus();
loadModels();
