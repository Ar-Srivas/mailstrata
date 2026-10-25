from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from backend.gmail_client import GOOGLE_OK, GmailAuth, account_email, fetch_inbox
from backend.rules import RuleContext
from backend.triage import run_triage

BASE_URL = os.getenv("MAILSTRATA_BASE_URL", "http://127.0.0.1:8000")
logger = logging.getLogger(__name__)


class ScanRequest(BaseModel):
    days: int = Field(30, ge=1, le=180)
    max_emails: int = Field(200, ge=10, le=500)


def build_router(db_connect, update_job, get_active_model, data_dir: Path, max_findings: int = 50) -> APIRouter:
    router = APIRouter()
    auth = GmailAuth(data_dir, f"{BASE_URL}/api/gmail/callback")

    with db_connect() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, value TEXT NOT NULL,
            UNIQUE(kind, value))""")

    def _set_account(email: str | None):
        with db_connect() as conn:
            if email:
                conn.execute("INSERT INTO app_config (key, value) VALUES ('gmail_email', ?) "
                             "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (email,))
            else:
                conn.execute("DELETE FROM app_config WHERE key = 'gmail_email'")

    def _account() -> str | None:
        with db_connect() as conn:
            row = conn.execute("SELECT value FROM app_config WHERE key = 'gmail_email'").fetchone()
        return row["value"] if row else None

    # ---------------- Gmail connection ----------------
    @router.get("/api/gmail/status")
    def gmail_status():
        connected = auth.credentials() is not None
        return {
            "packages_installed": GOOGLE_OK,
            "configured": auth.configured(),
            "connected": connected,
            "email": _account() if connected else None,
        }

    @router.post("/api/gmail/connect")
    def gmail_connect():
        if not GOOGLE_OK:
            raise HTTPException(503, "Gmail packages are missing. Install requirements-gmail.txt into the app's virtual environment and restart.")
        if not auth.configured():
            raise HTTPException(400, "Add data/credentials.json first. See GMAIL_SETUP.md.")
        return {"auth_url": auth.start()}

    @router.get("/api/gmail/callback")
    def gmail_callback(code: str = "", state: str = "", error: str = ""):
        if error or not code:
            return RedirectResponse("/?gmail=denied")
        try:
            creds = auth.finish(state, code)
            _set_account(account_email(creds))
        except Exception as exc:
            logger.error("Gmail OAuth callback failed (%s)", type(exc).__name__)
            return RedirectResponse("/?gmail=error")
        return RedirectResponse("/?gmail=connected")

    @router.post("/api/gmail/disconnect")
    def gmail_disconnect():
        auth.disconnect()
        _set_account(None)
        return {"status": "disconnected"}

    # ---------------- scan ----------------
    def _context(my_email: str, contacts) -> RuleContext:
        with db_connect() as conn:
            rows = conn.execute("SELECT kind, value FROM rules").fetchall()
        return RuleContext(
            my_email=my_email, contacts=contacts,
            vip={r["value"] for r in rows if r["kind"] == "vip"},
            muted={r["value"] for r in rows if r["kind"] == "mute"},
            keywords=[r["value"] for r in rows if r["kind"] == "keyword"],
        )

    def run_scan(job_id: str, days: int, max_emails: int, model: str):
        emails: list[dict] = []
        try:
            creds = auth.credentials()
            if creds is None:
                raise RuntimeError("Gmail is no longer connected. Connect it again.")
            update_job(job_id, status="fetching", progress_done=0, progress_total=0)
            emails, contacts, me, warnings = fetch_inbox(
                creds, days, max_emails,
                progress=lambda d, t: update_job(job_id, progress_done=d, progress_total=t))
            if not emails:
                raise RuntimeError(f"No emails found in the last {days} days.")
            update_job(job_id, status="processing", progress_done=0, progress_total=0)
            items, w, stats = run_triage(
                emails, _context(me, contacts), model=model,
                progress_cb=lambda d, t: update_job(job_id, progress_done=d, progress_total=t))
            warnings += w
            if len(items) > max_findings:
                warnings.append(f"Showing the top {max_findings} of {len(items)} findings.")
            threads = len({e.get("thread_key") for e in emails})
            report = {
                "job_id": job_id,
                "filename": f"Gmail ({me}), last {days} days",
                "source": "gmail",
                "emails_processed": len(emails),
                "threads_found": threads,
                "action_count": sum(1 for i in items if i["action_required"]),
                "high_priority_count": sum(1 for i in items if i["priority"] == "high"),
                "items": items[:max_findings],
                "warnings": warnings,
                "stats": stats,
            }
            update_job(job_id, status="completed", report_json=json.dumps(report))
        except Exception as exc:
            update_job(job_id, status="failed", error=f"Scan failed: {exc}")
        finally:
            emails.clear()

    @router.post("/api/gmail/scan")
    def gmail_scan(background_tasks: BackgroundTasks, req: ScanRequest):
        if auth.credentials() is None:
            raise HTTPException(401, "Connect Gmail first.")
        job_id = uuid.uuid4().hex[:12]
        with db_connect() as conn:
            conn.execute("INSERT INTO jobs (job_id, filename, status) VALUES (?, ?, 'queued')",
                         (job_id, f"Gmail, last {req.days} days"))
        background_tasks.add_task(run_scan, job_id, req.days, req.max_emails, get_active_model())
        return {"job_id": job_id, "status": "queued"}

    # ---------------- your rules (VIP / mute / keyword) ----------------
    @router.get("/api/rules")
    def list_rules():
        with db_connect() as conn:
            return [dict(r) for r in conn.execute("SELECT id, kind, value FROM rules ORDER BY id DESC")]

    @router.post("/api/rules")
    def add_rule(kind: str = Body(embed=True), value: str = Body(embed=True, min_length=2, max_length=120)):
        kind, value = kind.strip().lower(), value.strip().lower()
        if kind not in {"vip", "mute", "keyword"}:
            raise HTTPException(422, "kind must be vip, mute or keyword.")
        with db_connect() as conn:
            if kind in {"vip", "mute"}:  # a sender can't be both
                conn.execute("DELETE FROM rules WHERE kind IN ('vip','mute') AND value = ?", (value,))
            conn.execute("INSERT OR IGNORE INTO rules (kind, value) VALUES (?, ?)", (kind, value))
        return {"status": "saved", "kind": kind, "value": value}

    @router.delete("/api/rules/{rule_id}")
    def delete_rule(rule_id: int):
        with db_connect() as conn:
            conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
        return {"status": "deleted"}

    return router