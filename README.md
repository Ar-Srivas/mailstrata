# BuriedSignals

A local-first email backlog recovery MVP. Import an `.mbox` or `.eml` file, analyze messages with Gemma through Ollama, and generate a report of potential actions and important conversations.

## Requirements

- Python 3.11+
- Ollama installed and running
- A model pulled locally, for example `gemma3:4b`

## Setup (Windows PowerShell)

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
ollama pull gemma3:4b
```

If PowerShell blocks activation, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in that terminal, then activate again.

## Run

From the project root:

```powershell
python -m uvicorn backend.main:app --reload
```

Open http://127.0.0.1:8000

API docs: http://127.0.0.1:8000/docs

Check that Ollama is reachable at http://localhost:11434. You can change the model with environment variables:

```powershell
$env:OLLAMA_MODEL="gemma3:4b"
$env:OLLAMA_URL="http://localhost:11434"
python -m uvicorn backend.main:app --reload
```

## Supported imports

- `.eml`: one email
- `.mbox`: mailbox export containing multiple emails

The app does not currently connect to Gmail directly. For Gmail, export messages from Google Takeout to MBOX and import the MBOX file.

## Current limitations

- The MVP processes up to 500 emails by default (`MAX_EMAILS` in `backend/recovery_engine.py`).
- It groups messages using basic email headers; production-grade thread reconstruction needs provider thread IDs or better header logic.
- If Ollama is unavailable or returns malformed output, the app uses a basic heuristic fallback and reports a warning.
- The fallback is not AI analysis and should not be treated as reliable classification.
- AI output is a suggestion. Review before acting; no emails are modified or deleted.
- The default local setup keeps inference local. If deployed remotely, email content would be sent to that server unless the architecture is changed.

## Next improvements

1. Add robust Gmail OAuth with read-only scopes.
2. Add sender/date filters before model inference.
3. Store normalized messages and summaries in SQLite.
4. Improve thread grouping and deduplication.
5. Add source links and explicit evidence snippets for every proposed action.
6. Add tests with synthetic EML/MBOX fixtures.
