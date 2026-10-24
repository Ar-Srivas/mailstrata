from email.message import EmailMessage
from pathlib import Path
from backend.email_parser import parse_email_file


def test_parse_single_eml(tmp_path: Path):
    msg = EmailMessage()
    msg["From"] = "A Person <a@example.com>"
    msg["To"] = "b@example.com"
    msg["Subject"] = "Please send the report"
    msg["Date"] = "Tue, 20 Oct 2026 10:00:00 +0000"
    msg["Message-ID"] = "<message-1@example.com>"
    msg.set_content("Could you send the report by Friday?")
    path = tmp_path / "sample.eml"
    path.write_bytes(msg.as_bytes())

    result = parse_email_file(path)
    assert len(result) == 1
    assert result[0]["email_id"] == "message-1@example.com"
    assert result[0]["subject"] == "Please send the report"
    assert "send the report" in result[0]["body"]
