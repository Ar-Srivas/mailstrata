# MailStrata

MailStrata is a local-first email backlog recovery app. Import `.mbox`, `.eml`, or a ZIP containing those files; it parses and deduplicates messages locally, analyzes bounded batches with a local Ollama model, and creates a short evidence-backed Recovery Brief.

## Requirements

- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Python 3.11+
- Ollama installed and running for archive analysis
- `gemma3:1b` selected by default; other models already installed in Ollama can be selected in the app
- Optional Gmail integration: Google OAuth setup described in [GMAIL_SETUP.md](GMAIL_SETUP.md)

## Setup (Windows PowerShell)

```powershell
uv venv --python 3.11 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
```

MailStrata defaults to `gemma3:1b` and does not download models automatically. Install it with `ollama run gemma3:1b` before analyzing an archive. The commands below call the virtual environment's Python directly, so PowerShell activation is not required.

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
$env:OLLAMA_MODEL="gemma3:1b"
$env:OLLAMA_URL="http://localhost:11434"
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload
```

For example, if installed, choose `llama3.1:latest` from the selector to try a larger alternative. MailStrata only offers models reported by Ollama; it never pulls a model on selection.

## Supported imports

- `.eml`: one email
- `.mbox`: mailbox export containing multiple emails
- `.zip`: archive of `.eml` and/or `.mbox` files (up to 50 MB compressed and expanded)

Gmail scanning is optional and requires the Google OAuth setup in [GMAIL_SETUP.md](GMAIL_SETUP.md). Without it, import a `.mbox`, `.eml`, or `.zip` export instead. ZIP files are inspected in place; archive paths are never extracted to disk. Attachments are skipped. MailStrata does not use an external AI service by default; it analyzes mail with the local Ollama instance.

## Privacy and retention

- Imported bodies are held temporarily for parsing and inference; the app stores reports and short evidence excerpts in `data/buriedsignals.sqlite3` until **Clear saved data** is used.
- The upload is limited to 50 MB, ZIP file count to 1,000, and analysis to 500 messages by default (`MAX_EMAILS` in `backend/recovery_engine.py`).
- Ollama runs on localhost by default. The server also binds to localhost when started with the command above; do not expose it to an untrusted network.
- If you point `OLLAMA_URL` at a non-local service, email content will be sent to that service; use a trusted local endpoint for private archives.
- No telemetry or external monitoring is enabled. The interface uses system fonts and does not load external resources.
- If Ollama is unavailable, the app labels any rule-based findings as rules-only. If a model response is invalid, that email is excluded and reported; MailStrata does not fabricate an AI result for it.
- Findings are suggestions, not verified outstanding obligations. Dates are only shown when the source contains an explicit deadline. Review original emails before acting; MailStrata never sends, changes, or deletes email.
- The demo uses synthetic content only.

## Recovery Brief

Findings are grouped into **You may have forgotten**, **Deadlines to check**, **Waiting on you**, **Important decisions**, and **Worth revisiting**. Each finding includes sender, timestamp, source message ID, confidence, and a source excerpt when available. Search, filter, and export the brief as JSON.

## Development

```powershell
.\.venv\Scripts\python.exe -m pytest
```

The pipeline is modular: standard-library email parsing and local validation are separated from Ollama extraction in `backend/recovery_engine.py`. Candidate emails are analyzed one at a time. Before local model inference, identifying sender/date/message-ID fields are withheld and common email addresses, phone numbers, links, street addresses, and recognizable names are replaced with temporary tokens; validated results are mapped back locally. Output is schema-constrained and source-checked. Failed model analyses are reported and excluded rather than replaced with generated fallback findings. Email content is treated as untrusted input, and the model is explicitly told not to follow instructions embedded in messages.
