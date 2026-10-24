from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from backend.email_parser import parse_email_file
from backend.recovery_engine import analyze_emails, group_threads, MAX_EMAILS

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "buriedsignals.sqlite3"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

app = FastAPI(title="BuriedSignals API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db_connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                status TEXT NOT NULL,
                progress_done INTEGER DEFAULT 0,
                progress_total INTEGER DEFAULT 0,
                report_json TEXT,
                error TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)


init_db()


def update_job(job_id: str, **fields):
    allowed = {"status", "progress_done", "progress_total", "report_json", "error"}
    fields = {key: value for key, value in fields.items() if key in allowed}
    if not fields:
        return
    assignments = ", ".join(f"{key} = ?" for key in fields)
    with db_connect() as conn:
        conn.execute(
            f"UPDATE jobs SET {assignments} WHERE job_id = ?",
            (*fields.values(), job_id),
        )


def run_recovery(job_id: str, filename: str, emails: list[dict]):
    try:
        update_job(job_id, status="processing", progress_done=0, progress_total=min(len(emails), MAX_EMAILS))
        results, warnings = analyze_emails(
            emails,
            progress_callback=lambda done, total: update_job(
                job_id, progress_done=done, progress_total=total
            ),
        )
        threads = group_threads(emails[:MAX_EMAILS])
        report = {
            "job_id": job_id,
            "filename": filename,
            "emails_processed": len(results),
            "threads_found": len(threads),
            "action_count": sum(1 for item in results if item["action_required"]),
            "high_priority_count": sum(1 for item in results if item["priority"] == "high"),
            "items": results,
            "warnings": warnings,
        }
        update_job(job_id, status="completed", report_json=json.dumps(report))
    except Exception as exc:
        update_job(job_id, status="failed", error=str(exc))


@app.get("/")
def home():
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/api/health")
def health():
    return {"status": "ok", "ollama_url": os.getenv("OLLAMA_URL", "http://localhost:11434"),
            "model": os.getenv("OLLAMA_MODEL", "gemma3:4b")}


@app.post("/api/recovery/start")
async def start_recovery(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    filename = Path(file.filename or "emails").name
    suffix = Path(filename).suffix.lower()
    if suffix not in {".mbox", ".eml"}:
        raise HTTPException(status_code=400, detail="Upload an .mbox or .eml file.")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large. Maximum upload size is 50 MB.")
    if not content:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(content)
            temp_path = Path(tmp.name)
        emails = parse_email_file(temp_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse email file: {exc}") from exc
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)

    if not emails:
        raise HTTPException(status_code=400, detail="No email messages were found in that file.")

    job_id = uuid.uuid4().hex[:12]
    with db_connect() as conn:
        conn.execute(
            "INSERT INTO jobs (job_id, filename, status, progress_total) VALUES (?, ?, ?, ?)",
            (job_id, filename, "queued", min(len(emails), MAX_EMAILS)),
        )
    background_tasks.add_task(run_recovery, job_id, filename, emails)
    return {"job_id": job_id, "filename": filename, "emails_found": len(emails), "status": "queued"}


@app.get("/api/recovery/status/{job_id}")
def recovery_status(job_id: str):
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Recovery job not found.")
    return {
        "job_id": row["job_id"],
        "filename": row["filename"],
        "status": row["status"],
        "progress_done": row["progress_done"],
        "progress_total": row["progress_total"],
        "error": row["error"],
    }


@app.get("/api/recovery/report/{job_id}")
def recovery_report(job_id: str):
    with db_connect() as conn:
        row = conn.execute("SELECT status, report_json, error FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Recovery job not found.")
    if row["status"] != "completed":
        raise HTTPException(status_code=409, detail=f"Report is not ready. Current status: {row['status']}.")
    return json.loads(row["report_json"])


@app.get("/api/recovery/jobs")
def recent_jobs():
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT job_id, filename, status, created_at FROM jobs ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
    return [dict(row) for row in rows]
