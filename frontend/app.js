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
const warningsBox = document.querySelector("#warnings");
let currentReport = null;
let pollTimer = null;

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
  if (!["mbox", "eml"].includes(extension)) {
    showError("Please choose an .mbox or .eml file.");
    return;
  }
  // Assign dropped file to the file input using DataTransfer.
  const transfer = new DataTransfer();
  transfer.items.add(file);
  fileInput.files = transfer.files;
  fileLabel.textContent = file.name;
  hideError();
});

function showError(message) {
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
  progressMeta.textContent = total > 0 ? `${done.toLocaleString()} of ${total.toLocaleString()} messages analyzed` : "Waiting for processing to start";
  if (status === "queued") {
    progressTitle.textContent = "Preparing your recovery report";
    progressCopy.textContent = "Your emails have been parsed. Waiting for the AI pipeline…";
  } else if (status === "processing") {
    progressTitle.textContent = "Finding the signals in your inbox";
    progressCopy.textContent = "Gemma is reviewing small batches of messages. This may take a while on CPU.";
  }
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  hideError();
  const file = fileInput.files[0];
  if (!file) return showError("Choose an .mbox or .eml file first.");

  if (pollTimer) clearTimeout(pollTimer);
  reportSection.hidden = true;
  progressPanel.hidden = false;
  startButton.disabled = true;
  startButton.textContent = "Uploading…";

  try {
    const data = new FormData();
    data.append("file", file);
    const response = await fetch("/api/recovery/start", { method: "POST", body: data });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Could not start recovery.");

    setProgress(result.status);
    progressCopy.textContent = `Imported ${result.emails_found.toLocaleString()} messages from ${result.filename}.`;
    await pollStatus(result.job_id);
  } catch (error) {
    showError(error.message || "Unexpected error while starting recovery.");
  } finally {
    startButton.disabled = false;
    startButton.innerHTML = 'Start recovery <span>→</span>';
  }
});

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
      return;
    }
    if (status.status === "failed") throw new Error(status.error || "Recovery job failed.");
    pollTimer = setTimeout(() => pollStatus(jobId), 1200);
  } catch (error) {
    showError(error.message || "Could not check recovery status.");
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
  const items = currentReport.items.filter(item => {
    if (filter === "actions") return item.action_required;
    if (filter === "high") return item.priority === "high";
    return true;
  });
  if (!items.length) {
    resultsList.innerHTML = '<div class="empty-state">No messages match this filter.</div>';
    return;
  }
  resultsList.innerHTML = items.map(item => {
    const badges = [
      `<span class="badge ${escapeHtml(item.priority)}">${escapeHtml(item.priority)} priority</span>`,
      `<span class="badge">${escapeHtml(item.category)}</span>`,
      item.action_required ? '<span class="badge action">Potential action</span>' : ""
    ].join("");
    const actionText = item.action_required
      ? `<div class="action-box"><strong>Review:</strong> ${escapeHtml(item.action || "Check whether this message still needs a response.")}${item.deadline ? `<br><strong>Deadline mentioned:</strong> ${escapeHtml(item.deadline)}` : ""}</div>`
      : "";
    return `<article class="result-card">
      <div class="result-top">
        <div>
          <h3>${escapeHtml(item.subject || "(no subject)")}</h3>
          <p class="result-meta">${escapeHtml(item.sender)}${item.date ? " · " + escapeHtml(item.date) : ""}</p>
        </div>
        <span class="badge">ID ${escapeHtml(String(item.email_id).slice(0, 12))}</span>
      </div>
      <div class="badges">${badges}</div>
      <p class="result-summary">${escapeHtml(item.summary)}</p>
      ${actionText}
    </article>`;
  }).join("");
}
filterSelect.addEventListener("change", renderItems);

document.querySelector("#download-button").addEventListener("click", () => {
  if (!currentReport) return;
  const blob = new Blob([JSON.stringify(currentReport, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `buriedsignals-${currentReport.job_id}.json`;
  link.click();
  URL.revokeObjectURL(url);
});
