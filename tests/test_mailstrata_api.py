from __future__ import annotations

import json
import sqlite3
import zipfile
from email.message import EmailMessage

import pytest

from backend import main
from backend.gmail_routes import build_router
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
        lambda: ["gemma3:1b", "llama3.1:latest", "qwen2.5:1.5b"],
    )

    assert main.get_active_model() == "gemma3:1b"
    result = main.select_model("llama3.1:latest")

    assert result == {"active_model": "llama3.1:latest"}
    assert main.get_active_model() == "llama3.1:latest"


def test_legacy_qwen_default_is_migrated_to_gemma(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "settings.sqlite3")
    with main.db_connect() as conn:
        conn.execute(
            "CREATE TABLE app_config (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO app_config (key, value) VALUES ('ollama_model', ?)",
            ("qwen2.5:1.5b",),
        )

    main.init_db()

    assert main.get_active_model() == "gemma3:1b"


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
            "email_id": "mail_0001",
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
    assert results[0]["email_id"] == "source-1"
    assert results[0]["action_required"] is False
    assert results[0]["deadline"] is None
    assert results[0]["evidence"] == source["body"]
    assert any("unsupported deadlines" in warning for warning in warnings)


def test_ollama_generate_requests_schema_constrained_json(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return json.dumps({"response": '{"items": []}'}).encode()

    def fake_urlopen(request, timeout):
        captured.update(json.loads(request.data))
        return FakeResponse()

    monkeypatch.setattr(recovery_engine.urllib.request, "urlopen", fake_urlopen)

    assert recovery_engine._ollama_generate("prompt", model="gemma3:1b") == '{"items": []}'
    assert captured["format"] == recovery_engine.OLLAMA_RESPONSE_SCHEMA


def test_model_response_without_items_reports_shape():
    with pytest.raises(ValueError, match="response shape: results"):
        recovery_engine._parse_model_response('{"results": []}')


def test_model_deadline_trailing_punctuation_matches_source(monkeypatch):
    source = {
        "email_id": "source-2",
        "subject": "Review",
        "sender": "a@example.test",
        "date": "",
        "body": "Could you review this by Friday?",
    }
    response = {
        "items": [{
            "email_id": "mail_0001",
            "category": "deadline",
            "section": "deadline",
            "priority": "medium",
            "summary": "Review the request.",
            "action_required": True,
            "action": "Review the request.",
            "deadline": "by Friday?",
            "confidence": "medium",
            "evidence": "Could you review this by Friday?",
        }]
    }
    monkeypatch.setattr(
        recovery_engine,
        "_ollama_generate",
        lambda *_args, **_kwargs: json.dumps(response),
    )

    results, warnings = recovery_engine.analyze_emails([source])

    assert results[0]["deadline"] == "by Friday"
    assert not warnings


def test_model_sees_anonymized_single_email_and_result_restores_original_fields(monkeypatch):
    source = {
        "email_id": "private-user@example.test",
        "subject": "Hi John Smith, confirm by Friday",
        "sender": "John Smith <john.smith@example.test>",
        "date": "2026-10-25T10:00:00+00:00",
        "body": "Hi John Smith, call me at 415-555-0199 or email jane@example.test by Friday. Thanks,\nJohn Smith",
    }
    captured = {}

    def fake_generate(prompt, model=None):
        request = json.loads(prompt.split("Email records:\n", 1)[1])
        captured["request"] = request
        opaque_id = request[0]["email_id"]
        return json.dumps({"items": [{
            "email_id": opaque_id,
            "category": "action",
            "section": "waiting",
            "priority": "medium",
            "summary": "John should call Jane by Friday.",
            "action_required": True,
            "action": "Call Jane.",
            "deadline": "by Friday",
            "confidence": "high",
            "evidence": request[0]["body"],
        }]})

    monkeypatch.setattr(recovery_engine, "_ollama_generate", fake_generate)

    results, warnings = recovery_engine.analyze_emails([source])

    request_text = json.dumps(captured["request"])
    assert source["email_id"] not in request_text
    assert "John Smith" not in request_text
    assert "john.smith@example.test" not in request_text
    assert "jane@example.test" not in request_text
    assert "415-555-0199" not in request_text
    assert captured["request"][0]["email_id"] == "mail_0001"
    assert results[0]["email_id"] == source["email_id"]
    assert results[0]["sender"] == source["sender"]
    assert "John" in results[0]["summary"] and "Jane" in results[0]["summary"]
    assert results[0]["evidence"] == " ".join(source["body"].split())
    assert not warnings


def test_analyze_emails_sends_one_opaque_record_per_model_request(monkeypatch):
    sources = [
        {
            "email_id": f"private-{index}@example.test",
            "subject": f"Please review item {index}",
            "sender": f"Sender {index} <sender{index}@example.test>",
            "date": "2026-10-25",
            "body": f"Could you review item {index}?",
        }
        for index in range(1, 4)
    ]
    requests = []

    def fake_generate(prompt, model=None):
        record = json.loads(prompt.split("Email records:\n", 1)[1])[0]
        requests.append(record)
        return json.dumps({"items": [{
            "email_id": record["email_id"],
            "category": "action",
            "section": "waiting",
            "priority": "medium",
            "summary": "Review the item.",
            "action_required": True,
            "action": "Review the item.",
            "deadline": None,
            "confidence": "medium",
            "evidence": record["body"],
        }]})

    monkeypatch.setattr(recovery_engine, "_ollama_generate", fake_generate)

    results, warnings = recovery_engine.analyze_emails(sources)

    assert len(requests) == 3
    assert [request["email_id"] for request in requests] == [
        "mail_0001", "mail_0001", "mail_0001"
    ]
    assert [result["email_id"] for result in results] == [
        source["email_id"] for source in sources
    ]
    assert not warnings


def test_single_model_result_maps_back_even_if_model_changes_opaque_id(monkeypatch):
    source = {
        "email_id": "private-message-id",
        "subject": "Review",
        "sender": "a@example.test",
        "date": "",
        "body": "Please review this.",
    }
    result = {
        "email_id": "model-changed-id",
        "category": "action",
        "section": "task",
        "priority": "medium",
        "summary": "Review the message.",
        "action_required": True,
        "action": "Review it.",
        "deadline": None,
        "confidence": "medium",
        "evidence": "Please review this.",
    }
    monkeypatch.setattr(
        recovery_engine,
        "_ollama_generate",
        lambda *_args, **_kwargs: json.dumps({"items": [result]}),
    )

    results, warnings = recovery_engine.analyze_emails([source])

    assert len(results) == 1
    assert results[0]["email_id"] == source["email_id"]
    assert not warnings


def test_missing_model_result_is_not_replaced_with_fallback(monkeypatch):
    source = {
        "email_id": "source-missing",
        "subject": "Please confirm",
        "sender": "a@example.test",
        "date": "",
        "body": "Please confirm by Friday.",
    }
    monkeypatch.setattr(
        recovery_engine,
        "_ollama_generate",
        lambda *_args, **_kwargs: '{"items": []}',
    )

    results, warnings = recovery_engine.analyze_emails([source])

    assert results == []
    assert len(warnings) == 1
    assert "no results were fabricated" in warnings[0]
    assert source["email_id"] not in warnings[0]


def test_gmail_routes_are_registered_and_status_reports_setup(tmp_path):
    route_paths = main.app.openapi()["paths"]
    assert "/api/gmail/status" in route_paths
    assert "/api/gmail/connect" in route_paths
    assert "/api/gmail/callback" in route_paths
    assert "/api/gmail/scan" in route_paths

    db_path = tmp_path / "gmail.sqlite3"

    def test_db_connect():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    router = build_router(
        test_db_connect, lambda *_args, **_kwargs: None, lambda: "gemma3:1b", tmp_path
    )
    status_endpoint = next(
        route.endpoint for route in router.routes
        if route.path == "/api/gmail/status"
    )
    status = status_endpoint()

    assert set(status) == {"packages_installed", "configured", "connected", "email"}
    assert isinstance(status["packages_installed"], bool)
    assert status["configured"] is False
    assert status["connected"] is False
    assert status["email"] is None
