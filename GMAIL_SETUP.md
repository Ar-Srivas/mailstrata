# Connect Gmail (read-only)

MailStrata asks Google for exactly one permission: `gmail.readonly`. It can read your mail. It cannot send, delete, label or change anything. Your sign-in token is stored in `data/gmail_token.json` on this computer only. "Disconnect" in the app revokes it at Google and deletes the file.

## One-time setup (about 5 minutes)

1. Open https://console.cloud.google.com and create a project (any name).
2. **APIs & Services > Library**: search "Gmail API" and click **Enable**.
3. **APIs & Services > OAuth consent screen**: choose **External**, fill in an app name and your email, add the scope `.../auth/gmail.readonly`, and add your own Gmail address under **Test users**.
4. **APIs & Services > Credentials > Create credentials > OAuth client ID**:
   - Application type: **Web application**
   - Authorized redirect URI: `http://127.0.0.1:8000/api/gmail/callback`
5. Download the JSON and save it as `data/credentials.json`.
6. Install the Gmail packages into the same Python environment that runs MailStrata, then restart the app. For the README setup, use:

```
uv pip install --python .venv\Scripts\python.exe -r requirements-gmail.txt
.venv\Scripts\python.exe -m uvicorn backend.main:app --reload
```

7. Open http://127.0.0.1:8000 (use `127.0.0.1`, not `localhost`) and click **Connect Gmail**.

If Gmail still reports missing packages, the server is running under a different Python environment. Stop it and install the packages with that environment's Python, then restart the server. The app checks for the packages at startup.

## Good to know

- Google shows an "unverified app" warning because it's your own private app. Click **Advanced > Go to (app name)**. That's expected.
- While the consent screen is in **Testing** mode, Google expires the sign-in after 7 days. Just click Connect again.
- `gmail.readonly` is a "restricted" scope. It's fine for your own account, but sharing this app with other people would require Google's app verification.
- Add `data/credentials.json` and `data/gmail_token.json` to `.gitignore`. Never commit them.

## How the brief is built

1. Mail you sent, and threads where you already had the last word, are dropped.
2. Rules remove newsletters, promotions and automated mail, and score the rest: people you've written to before, direct mail, unread, questions, deadlines, bills, security notices, signatures.
3. Gemma (via Ollama) reads each remaining candidate individually (`MAX_LLM_EMAILS`, default 500).
4. Rules and AI are merged into one finding per thread, with a "Why it's here" explanation.

Before inference, sender/date/message-ID fields are withheld and common identifying details in the subject/body are replaced with temporary tokens. Results are mapped back locally for the report. If Ollama is unavailable, the app reports rules-only analysis; it does not invent AI results. If an individual model response is invalid or missing, that message is excluded from findings and reported in the run notes.

Use **Always important** or **Mute sender** on any finding to teach it. Those rules are stored locally (`/api/rules`) and apply to every future scan. They never change anything in Gmail.
