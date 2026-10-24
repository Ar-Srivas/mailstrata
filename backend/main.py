from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import tempfile
import uuid
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from fastapi import BackgroundTasks, Body, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.email_parser import parse_email_file
from backend.recovery_engine import MAX_EMAILS, OLLAMA_MODEL, OLLAMA_URL, analyze_emails, group_threads

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "buriedsignals.sqlite3"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_FILES = 1000
MAX_FINDINGS = 50

app = FastAPI(title="MailStrata API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.execute(
            "INSERT OR IGNORE INTO app_config (key, value) VALUES ('ollama_model', ?)",
            (os.getenv("OLLAMA_MODEL", OLLAMA_MODEL),),
        )


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


def get_active_model() -> str:
    with db_connect() as conn:
        row = conn.execute(
            "SELECT value FROM app_config WHERE key = 'ollama_model'"
        ).fetchone()
    return row["value"] if row else OLLAMA_MODEL


def get_installed_models() -> list[str]:
    configured_url = os.getenv("OLLAMA_URL", OLLAMA_URL)
    request = urllib.request.Request(f"{configured_url.rstrip('/')}/api/tags")
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not load models from Ollama at {configured_url}: {exc}") from exc
    model_entries = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(model_entries, list):
        raise RuntimeError("Ollama returned an invalid model list.")
    installed = sorted({
        str(item["name"])
        for item in model_entries
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    })
    completion_models = []
    for model_name in installed:
        show_request = urllib.request.Request(
            f"{configured_url.rstrip('/')}/api/show",
            data=json.dumps({"model": model_name}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(show_request, timeout=3) as response:
                details = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Could not check whether Ollama model {model_name} supports text generation: {exc}"
            ) from exc
        capabilities = details.get("capabilities") if isinstance(details, dict) else None
        if isinstance(capabilities, list) and "completion" in capabilities:
            completion_models.append(model_name)
    return completion_models


def _deduplicate(emails: list[dict]) -> list[dict]:
    unique: list[dict] = []
    seen: set[str] = set()
    for item in emails:
        identity = item.get("email_id", "").strip().lower()
        if not identity or identity.startswith("generated-"):
            identity = hashlib.sha256(
                "|".join(
                    (
                        item.get("sender", "").strip().lower(),
                        item.get("subject", "").strip().lower(),
                        item.get("date", "").strip(),
                        item.get("body", "").strip(),
                    )
                ).encode("utf-8", errors="replace")
            ).hexdigest()
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(item)
    return unique


def _parse_zip(content: bytes) -> list[dict]:
    emails: list[dict] = []
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ValueError("The ZIP archive is invalid or damaged.") from exc

    with archive:
        archive_entries = archive.infolist()
        if len(archive_entries) > MAX_ARCHIVE_FILES:
            raise ValueError(f"The ZIP contains too many files (maximum {MAX_ARCHIVE_FILES}).")
        files = [
            info for info in archive_entries
            if not info.is_dir() and Path(info.filename).suffix.lower() in {".mbox", ".eml"}
        ]
        expanded_size = sum(info.file_size for info in files)
        if expanded_size > MAX_ARCHIVE_BYTES:
            raise ValueError("The uncompressed email files exceed the 50 MB safety limit.")

        actual_expanded_size = 0
        for info in files:
            suffix = Path(info.filename).suffix.lower()
            with archive.open(info) as source:
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
                    temp_path = Path(temp_file.name)
                    temp_file.write(source.read(MAX_ARCHIVE_BYTES + 1))
            try:
                actual_expanded_size += temp_path.stat().st_size
                if actual_expanded_size > MAX_ARCHIVE_BYTES:
                    raise ValueError("The uncompressed email files exceed the 50 MB safety limit.")
                emails.extend(parse_email_file(temp_path))
            finally:
                temp_path.unlink(missing_ok=True)
    return emails


def run_recovery(job_id: str, filename: str, emails: list[dict], model: str | None = None):
    try:
        emails = _deduplicate(emails)
        update_job(
            job_id,
            status="processing",
            progress_done=0,
            progress_total=min(len(emails), MAX_EMAILS),
        )
        results, warnings = analyze_emails(
            emails,
            model=model,
            progress_callback=lambda done, total: update_job(
                job_id, progress_done=done, progress_total=total
            ),
        )
        threads = group_threads(emails[:MAX_EMAILS])
        findings = [item for item in results if item.get("section") != "skip"]
        section_order = {"deadline": 0, "waiting": 1, "task": 2, "decision": 3, "revisit": 4}
        priority_order = {"high": 0, "medium": 1, "low": 2}
        findings.sort(
            key=lambda item: (
                priority_order.get(item.get("priority", "low"), 2),
                section_order.get(item.get("section", "revisit"), 5),
                item.get("date", ""),
            )
        )
        if len(findings) > MAX_FINDINGS:
            warnings.append(
                f"Showing the {MAX_FINDINGS} highest-priority findings; "
                f"{len(findings) - MAX_FINDINGS} additional findings were left out of this brief."
            )
        report = {
            "job_id": job_id,
            "filename": filename,
            "emails_processed": len(results),
            "threads_found": len(threads),
            "action_count": sum(1 for item in findings if item["action_required"]),
            "high_priority_count": sum(1 for item in findings if item["priority"] == "high"),
            "items": findings[:MAX_FINDINGS],
            "warnings": warnings,
        }
        update_job(job_id, status="completed", report_json=json.dumps(report))
    except Exception as exc:
        update_job(job_id, status="failed", error=f"Recovery failed: {exc}")
    finally:
        emails.clear()


def _create_completed_job(filename: str, report: dict) -> str:
    job_id = uuid.uuid4().hex[:12]
    report["job_id"] = job_id
    with db_connect() as conn:
        conn.execute(
            "INSERT INTO jobs (job_id, filename, status, progress_done, progress_total, report_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                job_id,
                filename,
                "completed",
                report["emails_processed"],
                report["emails_processed"],
                json.dumps(report),
            ),
        )
    return job_id


@app.get("/")
def home():
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/api/health")
def health():
    configured_url = os.getenv("OLLAMA_URL", OLLAMA_URL)
    configured_model = get_active_model()
    available = False
    model_available = False
    try:
        request = urllib.request.Request(f"{configured_url.rstrip('/')}/api/tags")
        with urllib.request.urlopen(request, timeout=2) as response:
            available = True
            model_list = json.loads(response.read().decode("utf-8")).get("models", [])
            model_available = any(
                isinstance(item, dict)
                and str(item.get("name", "")) == configured_model
                for item in model_list
            )
    except (urllib.error.URLError, TimeoutError, OSError):
        pass
    except (json.JSONDecodeError, AttributeError, TypeError):
        model_available = False
    return {
        "status": "ok",
        "ollama_url": configured_url,
        "model": configured_model,
        "ollama_available": available,
        "model_available": model_available,
    }


@app.get("/api/models")
def list_models():
    try:
        models = get_installed_models()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"models": models, "active_model": get_active_model()}


@app.put("/api/config/model")
def select_model(model: str = Body(embed=True, min_length=1, max_length=200)):
    selected_model = model.strip()
    if not selected_model:
        raise HTTPException(status_code=422, detail="Model name cannot be empty.")
    try:
        models = get_installed_models()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if selected_model not in models:
        raise HTTPException(
            status_code=400,
            detail="That model is not installed in Ollama. Pull it locally before selecting it.",
        )
    with db_connect() as conn:
        conn.execute(
            "INSERT INTO app_config (key, value) VALUES ('ollama_model', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (selected_model,),
        )
    return {"active_model": selected_model}


@app.post("/api/recovery/start")
async def start_recovery(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    filename = Path(file.filename or "emails").name
    suffix = Path(filename).suffix.lower()
    if suffix not in {".mbox", ".eml", ".zip"}:
        raise HTTPException(status_code=400, detail="Upload an .mbox, .eml, or ZIP file.")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large. Maximum upload size is 50 MB.")
    if not content:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    try:
        if suffix == ".zip":
            emails = _parse_zip(content)
        else:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(content)
                temp_path = Path(tmp.name)
            try:
                emails = parse_email_file(temp_path)
            finally:
                temp_path.unlink(missing_ok=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (OSError, zipfile.BadZipFile) as exc:
        raise HTTPException(status_code=400, detail="Could not read the uploaded email archive.") from exc

    emails = _deduplicate(emails)
    if not emails:
        raise HTTPException(status_code=400, detail="No email messages were found in that file.")

    job_id = uuid.uuid4().hex[:12]
    with db_connect() as conn:
        conn.execute(
            "INSERT INTO jobs (job_id, filename, status, progress_total) VALUES (?, ?, ?, ?)",
            (job_id, filename, "queued", min(len(emails), MAX_EMAILS)),
        )
    background_tasks.add_task(run_recovery, job_id, filename, emails, get_active_model())
    return {
        "job_id": job_id,
        "filename": filename,
        "emails_found": len(emails),
        "status": "queued",
    }


@app.post("/api/recovery/demo")
def demo_recovery():
    items = [
        {
            "email_id": "demo-project-review",
            "subject": "Re: Spring launch review",
            "sender": "Maya Chen <maya@example.test>",
            "date": "2026-04-14T09:30:00+00:00",
            "category": "deadline",
            "section": "deadline",
            "priority": "high",
            "summary": "Maya asked for the revised launch review before the Friday planning meeting.",
            "action_required": True,
            "action": "Check whether the revised review was sent.",
            "deadline": "before the Friday planning meeting",
            "confidence": "high",
            "evidence": "Could you send the revised launch review before our Friday planning meeting?",
        },
        {
            "email_id": "demo-vendor-confirm",
            "subject": "Updated print estimate",
            "sender": "Jordan Lee <jordan@example.test>",
            "date": "2026-04-11T15:10:00+00:00",
            "category": "action",
            "section": "waiting",
            "priority": "medium",
            "summary": "The vendor is waiting for confirmation on the updated estimate.",
            "action_required": True,
            "action": "Review the estimate and confirm whether to proceed.",
            "deadline": None,
            "confidence": "medium",
            "evidence": "Please confirm if this estimate works and I can reserve the print slot.",
        },
        {
            "email_id": "demo-budget-decision",
            "subject": "Q2 budget approved",
            "sender": "Riley Morgan <riley@example.test>",
            "date": "2026-04-08T12:00:00+00:00",
            "category": "important",
            "section": "decision",
            "priority": "medium",
            "summary": "The team approved the Q2 research budget, with travel excluded.",
            "action_required": False,
            "action": None,
            "deadline": None,
            "confidence": "high",
            "evidence": "Approved: the Q2 research budget is $8,000, excluding travel.",
        },
        {
            "email_id": "demo-accessibility",
            "subject": "Notes from accessibility discussion",
            "sender": "Sam Patel <sam@example.test>",
            "date": "2026-04-02T10:15:00+00:00",
            "category": "conversation",
            "section": "revisit",
            "priority": "low",
            "summary": "The team discussed adding keyboard navigation to the next design review.",
            "action_required": False,
            "action": None,
            "deadline": None,
            "confidence": "medium",
            "evidence": "It would be useful to include keyboard navigation in the next design review.",
        },
    ]
    report = {
        "job_id": "",
        "filename": "synthetic-demo.eml",
        "emails_processed": 12,
        "threads_found": 7,
        "action_count": 2,
        "high_priority_count": 1,
        "items": items,
        "warnings": ["Demo content is synthetic and does not come from a real mailbox."],
    }
    job_id = _create_completed_job("synthetic-demo.eml", report)
    return {"job_id": job_id, "filename": "synthetic-demo.eml", "emails_found": 12, "status": "completed"}


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
        row = conn.execute(
            "SELECT status, report_json FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Recovery job not found.")
    if row["status"] != "completed":
        raise HTTPException(
            status_code=409,
            detail=f"Report is not ready. Current status: {row['status']}.",
        )
    return json.loads(row["report_json"])


@app.get("/api/recovery/jobs")
def recent_jobs():
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT job_id, filename, status, created_at FROM jobs "
            "ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
    return [dict(row) for row in rows]


@app.delete("/api/data")
def clear_local_data():
    conn = db_connect()
    try:
        conn.execute("PRAGMA secure_delete = ON")
        conn.execute("DELETE FROM jobs")
        conn.commit()
        conn.execute("VACUUM")
    finally:
        conn.close()
    return {"status": "cleared"}
