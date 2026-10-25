/* MailStrata front end — same API endpoints as before, new sidebar + step flow. */
const $ = sel => document.querySelector(sel);

const form = $("#upload-form");
const fileInput = $("#email-file");
const fileLabel = $("#file-label");
const fileSub = $("#file-sub");
const dropzone = $("#dropzone");
const uploadIcon = $("#upload-icon");
const startButton = $("#start-button");
const demoButton = $("#demo-button");
const errorMessage = $("#error-message");
const progressTitle = $("#progress-title");
const progressCopy = $("#progress-copy");
const progressBar = $("#progress-bar");
const progressMeta = $("#progress-meta");
const statusChip = $("#status-chip");
const resultsList = $("#results-list");
const tabsBox = $("#category-tabs");
const searchInput = $("#search-input");
const highOnly = $("#high-only");
const hideDone = $("#hide-done");
const warningsBox = $("#warnings");
const modelSelect = $("#model-select");
const modelFeedback = $("#model-feedback");
const refreshModelsButton = $("#refresh-models");

const views = {
  import: $("#view-import"),
  progress: $("#progress-panel"),
  report: $("#report-section"),
};
const stepButtons = [...document.querySelectorAll(".step")];

let currentReport = null;
let pollTimer = null;
let currentView = "import";
let activeCategory = "all";
let doneSet = new Set();

const sectionOrder = ["deadline", "waiting", "task", "decision", "revisit"];
const sectionNames = {
  deadline: "Deadlines to check",
  waiting: "Waiting on you",
  task: "You may have forgotten",
  decision: "Important decisions",
  revisit: "Worth revisiting",
};
const sectionNotes = {
  deadline: "Dates mentioned in your emails. Check they haven't passed.",
  waiting: "Someone may still be waiting for your reply.",
  task: "Things you said you'd do, or were asked to do.",
  decision: "Choices that were made or still need making.",
  revisit: "Lower urgency, but may be worth a look.",
};
const priorityRank = { high: 0, medium: 1, low: 2 };

/* ---------------- view switching ---------------- */
function showView(name) {
  currentView = name;
  for (const [key, el] of Object.entries(views)) el.hidden = key !== name;
  updateSteps();
  window.scrollTo({ top: 0 });
}
function updateSteps() {
  const order = ["import", "progress", "report"];
  const idx = order.indexOf(currentView);
  const processing = !!pollTimer || currentView === "progress";
  for (const btn of stepButtons) {
    const v = btn.dataset.view;
    const i = order.indexOf(v);
    btn.classList.toggle("is-current", v === currentView);
    btn.classList.toggle("is-done", i < idx);
    btn.setAttribute("aria-current", v === currentView ? "step" : "false");
    if (v === "import") btn.disabled = false;
    if (v === "progress") btn.disabled = !processing;
    if (v === "report") btn.disabled = !currentReport;
    const num = btn.querySelector(".step-num");
    num.textContent = btn.classList.contains("is-done") ? "✓" : String(i + 1);
  }
}
stepButtons.forEach(btn => btn.addEventListener("click", () => {
  if (!btn.disabled) showView(btn.dataset.view);
}));

/* ---------------- messages ---------------- */
function showError(message) {
  errorMessage.className = "message error";
  errorMessage.textContent = message;
  errorMessage.hidden = false;
}
function showSuccess(message) {
  errorMessage.className = "message success";
  errorMessage.textContent = message;
  errorMessage.hidden = false;
}
function hideError() {
  errorMessage.hidden = true;
  errorMessage.textContent = "";
}

/* ---------------- file picking ---------------- */
function setFile(file) {
  if (!file) {
    dropzone.classList.remove("has-file");
    uploadIcon.textContent = "↑";
    fileLabel.textContent = "Drop your file here, or click to choose";
    fileSub.textContent = ".mbox, .eml or .zip · up to 50 MB";
    return;
  }
  dropzone.classList.add("has-file");
  uploadIcon.textContent = "✓";
  fileLabel.textContent = file.name;
  fileSub.textContent = `${(file.size / 1048576).toFixed(1)} MB · ready. Click to change.`;
}
fileInput.addEventListener("change", () => { hideError(); setFile(fileInput.files[0]); });

dropzone.addEventListener("dragover", e => { e.preventDefault(); dropzone.classList.add("dragging"); });
dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragging"));
dropzone.addEventListener("drop", e => {
  e.preventDefault();
  dropzone.classList.remove("dragging");
  const file = e.dataTransfer.files[0];
  if (!file) return;
  const ext = file.name.toLowerCase().split(".").pop();
  if (!["mbox", "eml", "zip"].includes(ext)) {
    showError("That file type isn't supported. Choose an .mbox, .eml or .zip file.");
    return;
  }
  const transfer = new DataTransfer();
  transfer.items.add(file);
  fileInput.files = transfer.files;
  setFile(file);
  hideError();
});

/* ---------------- progress ---------------- */
function setProgress(status, done = 0, total = 0) {
  const labels = { fetching: "Downloading", queued: "Queued", processing: "Analyzing", completed: "Done", failed: "Failed" };
  statusChip.textContent = labels[status] || status;
  const percent = total > 0 ? Math.round((done / total) * 100) : 0;
  progressBar.style.width = `${percent}%`;
  progressBar.parentElement.setAttribute("aria-valuenow", String(percent));
  progressMeta.textContent = total > 0
    ? `${done.toLocaleString()} of ${total.toLocaleString()} emails ${status === "fetching" ? "downloaded" : "analyzed"} (${percent}%)`
    : "Waiting for processing to start";
  if (status === "fetching") {
    progressTitle.textContent = "Downloading from Gmail";
    progressCopy.textContent = "Reading your recent emails. Read-only, nothing is changed.";
  } else if (status === "queued") {
    progressTitle.textContent = "Getting ready";
    progressCopy.textContent = "Your emails were read on this computer. Waiting for the analysis to begin.";
  } else if (status === "processing") {
    progressTitle.textContent = "Reading your inbox";
    progressCopy.textContent = "The local AI is going through your messages in small batches.";
  } else if (status === "completed") {
    progressTitle.textContent = "Your brief is ready";
    progressCopy.textContent = "Opening your results…";
  }
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  hideError();
  const file = fileInput.files[0];
  if (!file) return showError("Choose an .mbox, .eml or .zip file first.");
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
    showError(error.message || "Something went wrong while starting. Try again.");
    endProcessing();
    showView("import");
  }
});

function beginProcessing() {
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = 1; // mark as active until the first poll schedules a real timer
  setProgress("queued");
  startButton.disabled = true;
  startButton.textContent = "Working…";
  demoButton.disabled = true;
  $("#gmail-scan-button").disabled = true;
  showView("progress");
}
function endProcessing() {
  pollTimer = null;
  startButton.disabled = false;
  startButton.textContent = "Find my to-dos";
  demoButton.disabled = false;
  $("#gmail-scan-button").disabled = false;
  updateSteps();
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
      endProcessing();
      openReport(report);
      return;
    }
    if (status.status === "failed") throw new Error(status.error || "Recovery job failed.");
    pollTimer = setTimeout(() => pollStatus(jobId), 1200);
  } catch (error) {
    showError(error.message || "Could not check progress. Try again.");
    endProcessing();
    showView("import");
  }
}

/* ---------------- done-tracking (per report, stored locally) ---------------- */
const doneKey = () => `mailstrata-done-${currentReport?.job_id}`;
function loadDone() {
  try { doneSet = new Set(JSON.parse(localStorage.getItem(doneKey()) || "[]")); }
  catch { doneSet = new Set(); }
}
function saveDone() {
  try { localStorage.setItem(doneKey(), JSON.stringify([...doneSet])); } catch { /* ignore */ }
}
const itemKey = item => `${item.email_id}|${item.section}`;

/* ---------------- report ---------------- */
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[c]);
}

function openReport(report) {
  currentReport = report;
  activeCategory = "all";
  searchInput.value = "";
  highOnly.checked = false;
  hideDone.checked = false;
  loadDone();

  $("#report-subtitle").textContent =
    `${report.filename} · ${report.emails_processed.toLocaleString()} emails read`;
  $("#stat-emails").textContent = report.emails_processed.toLocaleString();
  $("#stat-actions").textContent = report.action_count.toLocaleString();
  $("#stat-high").textContent = report.high_priority_count.toLocaleString();
  $("#stat-threads").textContent = report.threads_found.toLocaleString();

  warningsBox.hidden = !report.warnings?.length;
  warningsBox.innerHTML = report.warnings?.length
    ? `<strong>Notes from this run</strong><ul>${report.warnings.map(w => `<li>${escapeHtml(w)}</li>`).join("")}</ul>`
    : "";

  showView("report");
  renderAll();
}

function renderAll() {
  renderSummary();
  renderTabs();
  renderItems();
}

function renderSummary() {
  const items = currentReport.items;
  const open = items.filter(i => !doneSet.has(itemKey(i)));
  const urgent = open.filter(i => i.priority === "high").length;
  const line = $("#summary-line");
  if (!items.length) line.textContent = "Nothing needs your attention. Your inbox looks clear.";
  else if (!open.length) line.textContent = "All caught up. You've ticked off everything.";
  else if (urgent) line.textContent = `${open.length} things to look at. ${urgent} ${urgent === 1 ? "is" : "are"} high priority, so start there.`;
  else line.textContent = `${open.length} things worth a look. None are high priority.`;

  const s = currentReport.stats;
  const ignored = s ? (s.noise || 0) + (s.already_answered || 0) + (s.from_you || 0) : 0;
  const ig = $("#ignored-line");
  ig.hidden = !ignored;
  if (ignored) {
    ig.textContent = `Skipped ${ignored.toLocaleString()} emails: newsletters, promotions, automated mail, and threads you already answered.`;
  }
}

function renderTabs() {
  const counts = { all: currentReport.items.length };
  for (const key of sectionOrder) counts[key] = currentReport.items.filter(i => i.section === key).length;
  const tabs = [["all", "All"], ...sectionOrder.map(k => [k, sectionNames[k]])];
  tabsBox.innerHTML = tabs.map(([key, name]) => `
    <button type="button" role="tab" class="tab" data-cat="${key}"
      aria-selected="${key === activeCategory}" ${counts[key] === 0 && key !== "all" ? "disabled" : ""}>
      ${escapeHtml(name)} <span>${counts[key] || 0}</span>
    </button>`).join("");
}
tabsBox.addEventListener("click", e => {
  const tab = e.target.closest(".tab");
  if (!tab || tab.disabled) return;
  activeCategory = tab.dataset.cat;
  renderTabs();
  renderItems();
});

function getFiltered() {
  const query = searchInput.value.trim().toLowerCase();
  return currentReport.items.filter(item => {
    if (activeCategory !== "all" && item.section !== activeCategory) return false;
    if (highOnly.checked && item.priority !== "high") return false;
    if (hideDone.checked && doneSet.has(itemKey(item))) return false;
    if (!query) return true;
    const text = [item.subject, item.sender, item.summary, item.evidence, item.action]
      .filter(Boolean).join(" ").toLowerCase();
    return text.includes(query);
  }).sort((a, b) => (priorityRank[a.priority] ?? 3) - (priorityRank[b.priority] ?? 3));
}

function renderItems() {
  if (!currentReport) return;
  const items = getFiltered();
  if (!items.length) {
    resultsList.innerHTML = `<div class="empty-state">${
      currentReport.items.length
        ? "Nothing matches these filters. Clear the search or pick \"All\"."
        : "No action items were found in this archive."
    }</div>`;
    return;
  }

  // "Start here": top 3 open high-priority items, only on the unfiltered All view
  const unfiltered = activeCategory === "all" && !searchInput.value.trim() && !highOnly.checked;
  const top = unfiltered
    ? items.filter(i => i.priority === "high" && !doneSet.has(itemKey(i))).slice(0, 3)
    : [];
  const topKeys = new Set(top.map(itemKey));
  const rest = items.filter(i => !topKeys.has(itemKey(i)));

  let html = "";
  if (top.length) {
    html += `<section class="start-here">
      <h2>Start here</h2>
      <p>The most urgent ${top.length === 1 ? "item" : top.length + " items"}. Do these first.</p>
      <div class="cards">${top.map(renderItem).join("")}</div>
    </section>`;
  }

  const grouped = new Map();
  for (const item of rest) {
    const key = sectionNames[item.section] ? item.section : "revisit";
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(item);
  }
  for (const key of sectionOrder) {
    const list = grouped.get(key);
    if (!list) continue;
    html += `<section class="group">
      <h3>${escapeHtml(sectionNames[key])} <span>${list.length}</span></h3>
      <p class="group-note">${escapeHtml(sectionNotes[key])}</p>
      <div class="cards">${list.map(renderItem).join("")}</div>
    </section>`;
  }
  resultsList.innerHTML = html;
}

function renderItem(item) {
  const key = itemKey(item);
  const done = doneSet.has(key);
  const prio = ["high", "medium", "low"].includes(item.priority) ? item.priority : "low";
  const senderName = (item.sender || "")
    .replace(/<[^>]*>/g, "")
    .replace(/\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi, "")
    .trim()
    .replace(/\s+/g, " ") || "Sender";
  const next = item.action_required
    ? `<div class="action-box"><strong>Next step:</strong> ${escapeHtml(item.action || "Check whether this still needs attention.")}</div>`
    : "";
  const deadline = item.action_required && item.deadline
    ? `<div class="action-box"><strong>Deadline mentioned:</strong> ${escapeHtml(item.deadline)}</div>`
    : "";
  const evidence = item.evidence
    ? `<details class="evidence"><summary>Show the original text</summary><blockquote>${escapeHtml(item.evidence)}</blockquote><code>Message ID: ${escapeHtml(item.email_id)}</code></details>`
    : `<details class="evidence"><summary>Show message ID</summary><code>${escapeHtml(item.email_id)}</code></details>`;
  const safeLink = typeof item.link === "string" && item.link.startsWith("https://mail.google.com/")
    ? `<a class="open-link" href="${escapeHtml(item.link)}" target="_blank" rel="noopener noreferrer">Open in Gmail</a>` : "";
  const why = item.reasons?.length
    ? `<p class="why"><strong>Why it's here:</strong> ${item.reasons.map(escapeHtml).join(", ")}</p>` : "";
  const sender = escapeHtml(item.sender_email || "");
  const tools = item.sender_email ? `<div class="item-tools">${safeLink}
      <button type="button" class="tool-btn rule-btn" data-kind="vip" data-sender="${sender}">Always important</button>
      <button type="button" class="tool-btn rule-btn" data-kind="mute" data-sender="${sender}">Mute sender</button></div>` : "";
  return `<article class="result-card p-${prio} ${done ? "is-done" : ""}">
    <input type="checkbox" class="done-check" data-key="${escapeHtml(key)}" ${done ? "checked" : ""}
      aria-label="Mark as done: ${escapeHtml(item.subject || "(no subject)")}">
    <div class="result-body">
      <div class="result-top">
        <h4>${escapeHtml(item.subject || "(no subject)")}</h4>
        <span class="badge ${prio}">${prio === "high" ? "High priority" : prio === "medium" ? "Medium" : "Low"}</span>
        ${item.confidence ? `<span class="badge">${escapeHtml(item.confidence)} confidence</span>` : ""}
      </div>
      <p class="result-meta">${escapeHtml(senderName)}${item.date ? " · " + escapeHtml(item.date) : ""}</p>
      <p class="result-summary">${escapeHtml(item.summary)}</p>
      ${next}${deadline}
      ${why}
      ${evidence}
      ${tools}
    </div>
  </article>`;
}

resultsList.addEventListener("change", e => {
  const box = e.target.closest(".done-check");
  if (!box) return;
  if (box.checked) doneSet.add(box.dataset.key); else doneSet.delete(box.dataset.key);
  saveDone();
  renderSummary();

  renderItems();
});

searchInput.addEventListener("input", renderItems);
highOnly.addEventListener("change", renderItems);
hideDone.addEventListener("change", renderItems);

/* ---------------- demo / clear / export / new ---------------- */
demoButton.addEventListener("click", async () => {
  hideError();
  demoButton.disabled = true;
  demoButton.textContent = "Loading demo…";
  try {
    const response = await fetch("/api/recovery/demo", { method: "POST" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Could not load the demo.");
    const reportResponse = await fetch(`/api/recovery/report/${encodeURIComponent(result.job_id)}`);
    const report = await reportResponse.json();
    if (!reportResponse.ok) throw new Error(report.detail || "Could not load the demo report.");
    openReport(report);
  } catch (error) {
    showError(error.message || "Could not load the demo.");
  } finally {
    demoButton.disabled = false;
    demoButton.textContent = "No file? Try the demo";
  }
});

$("#clear-button").addEventListener("click", async () => {
  if (!window.confirm("Delete all saved reports and demo results from this app? This can't be undone.")) return;
  hideError();
  try {
    const response = await fetch("/api/data", { method: "DELETE" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Could not delete saved data.");
    currentReport = null;
    showView("import");
    showSuccess("Saved reports were deleted.");
  } catch (error) {
    showError(error.message || "Could not delete saved data.");
  }
});

$("#new-button").addEventListener("click", () => {
  fileInput.value = "";
  setFile(null);
  hideError();
  showView("import");
});

$("#download-button").addEventListener("click", () => {
  if (!currentReport) return;
  const blob = new Blob([JSON.stringify(currentReport, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `mailstrata-${currentReport.job_id}.json`;
  link.click();
  URL.revokeObjectURL(url);
});

/* ---------------- local model (Ollama) ---------------- */
async function loadModelStatus() {
  const box = $("#model-status");
  const text = $("#model-status-text");
  try {
    const response = await fetch("/api/health");
    const health = await response.json();
    if (!response.ok) throw new Error("Status unavailable");
    text.textContent = health.model_available
      ? `AI ready · ${health.model}`
      : health.ollama_available
        ? `Model ${health.model} isn't installed`
        : "Ollama not found · demo still works";
    box.classList.toggle("offline", !health.model_available);
  } catch {
    text.textContent = "AI status unavailable";
    box.classList.add("offline");
  }
}

async function loadModels() {
  modelFeedback.classList.remove("offline");
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
      ? "A new choice applies to the next run, not one already in progress."
      : "No Ollama models installed yet.";
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
  modelFeedback.textContent = "Switching model…";
  try {
    const response = await fetch("/api/config/model", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: modelSelect.value }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Could not change the model.");
    modelFeedback.textContent = `Now using ${result.active_model}. Saved on this computer.`;
    await loadModelStatus();
  } catch (error) {
    modelFeedback.textContent = error.message || "Could not change the model.";
    modelFeedback.classList.add("offline");
    await loadModels();
  } finally {
    modelSelect.disabled = false;
  }
});
refreshModelsButton.addEventListener("click", loadModels);

/* ---------------- toast + your rules (VIP / mute) ---------------- */
let toastTimer = null;
function showToast(message) {
  const t = $("#toast");
  t.textContent = message;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 3500);
}

resultsList.addEventListener("click", async e => {
  const btn = e.target.closest(".rule-btn");
  if (!btn) return;
  const { kind, sender } = btn.dataset;
  btn.disabled = true;
  try {
    const response = await fetch("/api/rules", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind, value: sender }),
    });
    if (!response.ok) throw new Error((await response.json()).detail || "Could not save that rule.");
    if (kind === "mute") currentReport.items = currentReport.items.filter(i => i.sender_email !== sender);
    else currentReport.items.forEach(i => { if (i.sender_email === sender) i.priority = "high"; });
    renderAll();
    showToast(kind === "mute"
      ? `Muted ${sender}. Future scans will ignore them.`
      : `${sender} will always be high priority.`);
  } catch (err) {
    btn.disabled = false;
    showToast(err.message || "Could not save that rule.");
  }
});

/* ---------------- Gmail ---------------- */
async function loadGmailStatus() {
  const setup = $("#gmail-setup"), connect = $("#gmail-connect"), ready = $("#gmail-ready");
  [setup, connect, ready].forEach(el => { el.hidden = true; });
  try {
    const response = await fetch("/api/gmail/status");
    if (!response.ok) {
      if (response.status === 404) {
        throw new Error("The running server does not have the Gmail routes. Stop it and restart MailStrata from this project folder.");
      }
      throw new Error(`Gmail status check failed (HTTP ${response.status}).`);
    }
    const s = await response.json();
    if (!s.packages_installed) {
      $("#gmail-setup-msg").textContent = "Gmail packages are missing from the Python environment running MailStrata. Install requirements-gmail.txt into that environment, then restart the server (see GMAIL_SETUP.md).";
      setup.hidden = false;
    } else if (!s.configured) {
      $("#gmail-setup-msg").textContent = "Gmail isn't set up yet. Add your Google credentials file to the data folder.";
      setup.hidden = false;
    } else if (!s.connected) {
      connect.hidden = false;
    } else {
      $("#gmail-account").textContent = s.email || "your Gmail account";
      ready.hidden = false;
    }
  } catch (error) {
    $("#gmail-setup-msg").textContent = error.message || "Couldn't check Gmail status.";
    setup.hidden = false;
  }
}

$("#gmail-connect-button").addEventListener("click", async () => {
  hideError();
  try {
    const response = await fetch("/api/gmail/connect", { method: "POST" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Could not start Gmail sign-in.");
    window.location.href = result.auth_url;
  } catch (error) {
    showError(error.message || "Could not start Gmail sign-in.");
  }
});

$("#gmail-disconnect").addEventListener("click", async () => {
  if (!window.confirm("Disconnect Gmail? MailStrata will lose access and delete its saved sign-in.")) return;
  try {
    const response = await fetch("/api/gmail/disconnect", { method: "POST" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Could not disconnect Gmail.");
    await loadGmailStatus();
  } catch (error) {
    showError(error.message || "Could not disconnect Gmail.");
  }
});

$("#gmail-scan-button").addEventListener("click", async () => {
  hideError();
  beginProcessing();
  try {
    const response = await fetch("/api/gmail/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ days: Number($("#gmail-days").value), max_emails: 200 }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Could not start the scan.");
    setProgress("fetching");
    await pollStatus(result.job_id);
  } catch (error) {
    showError(error.message || "Could not start the scan.");
    endProcessing();
    showView("import");
  }
});

/* ---------------- init ---------------- */
const gmailParam = new URLSearchParams(window.location.search).get("gmail");
if (gmailParam) {
  history.replaceState(null, "", window.location.pathname);
  if (gmailParam === "connected") showSuccess("Gmail connected. Pick a time range and scan.");
  else if (gmailParam === "denied") showError("Gmail access was not granted. Nothing was connected.");
  else showError("Gmail sign-in failed. Check the server log and verify the OAuth credentials and redirect URI, then try again.");
}












loadGmailStatus();
showView("import");
loadModelStatus();
loadModels();