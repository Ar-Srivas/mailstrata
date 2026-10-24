# MailStrata

MailStrata is a local-first email backlog recovery app. Import `.mbox`, `.eml`, or a ZIP containing those files; it parses and deduplicates messages locally, analyzes bounded batches with a local Ollama model, and creates a short evidence-backed Recovery Brief.

## Requirements

- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Python 3.11+
- Ollama installed and running for archive analysis
- `qwen2.5:1.5b` selected by default; other models already installed in Ollama can be selected in the app

## Setup (Windows PowerShell)

```powershell
uv venv --python 3.11 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
```

MailStrata defaults to `qwen2.5:1.5b` and does not download models automatically. If needed, install it separately with `ollama pull qwen2.5:1.5b`. The commands below call the virtual environment's Python directly, so PowerShell activation is not required.

## Run

From the project root:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload
```

Open http://127.0.0.1:8000. Use **Try synthetic demo** to explore the brief without Ollama or importing personal mail.

API docs: http://127.0.0.1:8000/docs

Check that Ollama is reachable at http://localhost:11434. The **Local Ollama model** selector lists models already installed on your Ollama instance. Switch models without restarting MailStrata; the selection is saved locally and applies to the next recovery. A recovery already in progress continues with the model it started with. Use **Refresh models** after installing a model.

You may set an initial model or Ollama endpoint with environment variables:

```powershell
$env:OLLAMA_MODEL="qwen2.5:1.5b"
$env:OLLAMA_URL="http://localhost:11434"
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload
```

For example, if installed, choose `llama3.1:latest` from the selector to try a larger alternative. MailStrata only offers models reported by Ollama; it never pulls a model on selection.

## Supported imports

- `.eml`: one email
- `.mbox`: mailbox export containing multiple emails
- `.zip`: archive of `.eml` and/or `.mbox` files (up to 50 MB compressed and expanded)

The app does not connect to Gmail or any external AI service. For Gmail, export messages from Google Takeout to MBOX and import the MBOX file. ZIP files are inspected in place; archive paths are never extracted to disk. Attachments are skipped.

## Privacy and retention

- Imported bodies are held temporarily for parsing and inference; the app stores reports and short evidence excerpts in `data/buriedsignals.sqlite3` until **Clear saved data** is used.
- The upload is limited to 50 MB, ZIP file count to 1,000, and analysis to 500 messages by default (`MAX_EMAILS` in `backend/recovery_engine.py`).
- Ollama runs on localhost by default. The server also binds to localhost when started with the command above; do not expose it to an untrusted network.
- If you point `OLLAMA_URL` at a non-local service, email content will be sent to that service; use a trusted local endpoint for private archives.
- No telemetry or external monitoring is enabled. The interface uses system fonts and does not load external resources.
- If Ollama is unavailable or returns malformed output, a basic local heuristic fallback is used and a warning is included. The fallback is not AI analysis and should not be treated as reliable classification.
- Findings are suggestions, not verified outstanding obligations. Dates are only shown when the source contains an explicit deadline. Review original emails before acting; MailStrata never sends, changes, or deletes email.
- The demo uses synthetic content only.

## Recovery Brief

Findings are grouped into **You may have forgotten**, **Deadlines to check**, **Waiting on you**, **Important decisions**, and **Worth revisiting**. Each finding includes sender, timestamp, source message ID, confidence, and a source excerpt when available. Search, filter, and export the brief as JSON.

## Development

```powershell
.\.venv\Scripts\python.exe -m pytest
```

The pipeline is modular: standard-library email parsing and local validation are separated from Ollama extraction in `backend/recovery_engine.py`. Model output is constrained to JSON, bounded batches, known section values, and source-verified excerpts. Email content is treated as untrusted input, and the model is explicitly told not to follow instructions embedded in messages.
