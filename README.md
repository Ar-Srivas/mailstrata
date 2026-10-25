# MailStrata

**Turn an overwhelming inbox into a short, evidence-backed list of things worth revisiting.**

MailStrata is a local-first email recovery app. Import an email archive—or optionally connect Gmail read-only—and get a focused brief of possible replies, tasks, deadlines, decisions, and conversations to revisit. Analysis runs through a local Ollama model.

## What it does

- Imports `.eml`, `.mbox`, and ZIP archives containing email files.
- Uses local rules and Gemma to prioritize messages and explain why they were surfaced.
- Shows source excerpts and links to original Gmail messages when available.
- Keeps reports and preferences on your machine; it never sends, changes, or deletes email.
- Includes a synthetic demo that works without connecting an inbox.

Findings are suggestions, not verified obligations. Review the original message before acting.

## Run locally

Requirements: Python 3.11+, [uv](https://docs.astral.sh/uv/getting-started/installation/), and [Ollama](https://ollama.com/) for AI analysis.

In Windows PowerShell, from the project folder:

```powershell
uv venv --python 3.11 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
ollama run gemma3:1b
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload
```

Open http://127.0.0.1:8000 and try the demo or import an archive. Ollama models are not downloaded automatically; use the model selector to choose another model already installed.

## Optional Gmail connection

Gmail access is read-only and requires a Google OAuth client. See [GMAIL_SETUP.md](GMAIL_SETUP.md) for setup steps. Use the redirect URL `http://127.0.0.1:8000/api/gmail/callback`. OAuth credentials and tokens belong in the ignored `data/` directory; never commit them.

## Privacy

Email parsing and report storage are local. Before sending a message to the local model, MailStrata withholds sender, date, and message ID, and replaces common identifying details with temporary tokens. This is best-effort redaction, not a guarantee that every personal detail is removed. Pointing `OLLAMA_URL` at a remote server sends email content there. Reports remain in `data/buriedsignals.sqlite3` until cleared.

## Contribute

MailStrata is a work in progress, and contributions are welcome. Useful areas to explore:

- Add test fixtures for tricky MIME, HTML-only, and malformed email formats.
- Improve privacy-preserving redaction and test its limits.
- Make rule explanations and deadline handling more accurate.
- Improve accessibility, keyboard navigation, and small-screen layouts.
- Add mocked tests for Gmail OAuth, pagination, and API failures.
- Improve setup guidance and report confusing behavior with a minimal reproduction.

For changes, open an issue or pull request with a focused scope and tests where practical. Please use synthetic or sanitized email examples—never contribute real inbox content, OAuth credentials, or tokens.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```
