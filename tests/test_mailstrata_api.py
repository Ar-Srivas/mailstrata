from __future__ import annotations

import json
import zipfile
from email.message import EmailMessage

import pytest

from backend import main
from backend import recovery_engine


def _message(message_id: str, subject: str = "A useful note") -> bytes:
    message = EmailMessage()
    message["From"] = "A Person <a@example.test>"
    message["To"] = "b@example.test"
    message["Subject"] = subject
    message["Message-ID"] = f"<{message_id}>"
    message.set_content("Please review this note.")
    return message.as_bytes()


def test_parse_zip_with_eml_and_mbox(tmp_path):
    archive_path = tmp_path / "mail.zip"
    mbox_message = b"From sender@example.test Sat Jan  1 00:00:00 2022\n" + _message(
        "two@example.test", "An archived note"
    )
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("nested/message.eml", _message("one@example.test"))
        archive.writestr("nested/archive.mbox", mbox_message)
        archive.writestr("nested/not-mail.txt", "ignored")

    parsed = main._parse_zip(archive_path.read_bytes())
    assert {item["email_id"] for item in parsed} == {
        "one@example.test", "two@example.test"
    }


def test_parse_zip_rejects_invalid_archive():
    with pytest.raises(ValueError, match="invalid or damaged"):
        main._parse_zip(b"not a zip")


def test_deduplicate_uses_message_id():
    first = {"email_id": "same@example.test", "body": "first"}
    duplicate = {"email_id": "same@example.test", "body": "duplicate copy"}
    assert main._deduplicate([first, duplicate]) == [first]


def test_deduplicate_generated_ids_by_message_content():
    first = {
        "email_id": "generated-first",
        "sender": "A Person",
        "subject": "A note",
        "date": "2026-01-01",
        "body": "Same content",
    }
    duplicate = {**first, "email_id": "generated-second"}
    assert main._deduplicate([first, duplicate]) == [first]


def test_demo_report_is_synthetic_and_persisted(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "reports.sqlite3")
    main.init_db()

    result = main.demo_recovery()
    report = main.recovery_report(result["job_id"])

    assert result["status"] == "completed"
    assert report["filename"] == "synthetic-demo.eml"
    assert {item["section"] for item in report["items"]} == {
        "deadline", "waiting", "decision", "revisit"
    }
    assert report["warnings"] == [
        "Demo content is synthetic and does not come from a real mailbox."
    ]
    assert main.clear_local_data() == {"status": "cleared"}
    assert main.recent_jobs() == []


def test_model_switch_is_persisted_locally(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "settings.sqlite3")
    main.init_db()
    monkeypatch.setattr(
        main,
        "get_installed_models",
        lambda: ["llama3.1:latest", "qwen2.5:1.5b"],
    )

    assert main.get_active_model() == "qwen2.5:1.5b"
    result = main.select_model("llama3.1:latest")

    assert result == {"active_model": "llama3.1:latest"}
    assert main.get_active_model() == "llama3.1:latest"


def test_model_switch_rejects_models_not_installed(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "settings.sqlite3")
    main.init_db()
    monkeypatch.setattr(main, "get_installed_models", lambda: ["qwen2.5:1.5b"])

    with pytest.raises(main.HTTPException) as error:
        main.select_model("not-installed:latest")

    assert error.value.status_code == 400


def test_model_deadline_and_evidence_must_match_source(monkeypatch):
    source = {
        "email_id": "source-1",
        "subject": "Review",
        "sender": "a@example.test",
        "date": "",
        "body": "Please reply by Friday to approve the draft.",
    }
    response = {
        "items": [{
            "email_id": "source-1",
            "category": "deadline",
            "section": "deadline",
            "priority": "high",
            "summary": "Review the draft.",
            "action_required": "false",
            "action": "Review the draft.",
            "deadline": "next month",
            "confidence": "high",
            "evidence": "The meeting is next month.",
        }]
    }
    requested_models = []

    def fake_generate(_prompt, model=None):
        requested_models.append(model)
        return json.dumps(response)

    monkeypatch.setattr(recovery_engine, "_ollama_generate", fake_generate)

    results, warnings = recovery_engine.analyze_emails([source], model="qwen2.5:1.5b")

    assert requested_models == ["qwen2.5:1.5b"]
    assert results[0]["action_required"] is False
    assert results[0]["deadline"] is None
    assert results[0]["evidence"] == source["body"]
    assert any("deadline not present" in warning for warning in warnings)
