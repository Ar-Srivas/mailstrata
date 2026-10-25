from __future__ import annotations

import email
from html.parser import HTMLParser
import hashlib
import mailbox
import re
from email import policy
from email.message import Message
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterable


MAX_BODY_CHARS = 7000


class _EmailHTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_tags: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        if tag in {"script", "style", "head"}:
            self._ignored_tags.append(tag)
        elif tag in {"br", "p", "div", "li", "tr", "td", "th"} and not self._ignored_tags:
            self.parts.append("\n")

    def handle_endtag(self, tag: str):
        if tag in self._ignored_tags:
            self._ignored_tags.remove(tag)
        elif tag in {"p", "div", "li", "tr"} and not self._ignored_tags:
            self.parts.append("\n")

    def handle_data(self, data: str):
        if not self._ignored_tags:
            self.parts.append(data)


def _html_to_text(html: str) -> str:
    parser = _EmailHTMLText()
    parser.feed(html)
    parser.close()
    return "".join(parser.parts)


def _decode_part(part: Message) -> str:
    try:
        payload = part.get_content()
        if isinstance(payload, str):
            return payload
    except Exception:
        pass

    raw = part.get_payload(decode=True)
    if raw is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _body_from_message(msg: Message) -> str:
    chunks: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_disposition() == "attachment":
                continue
            content_type = part.get_content_type()
            if content_type == "text/plain":
                chunks.append(_decode_part(part))
        # Some emails contain only HTML.
        if not chunks:
            for part in msg.walk():
                if part.get_content_type() == "text/html" and part.get_content_disposition() != "attachment":
                    html = _decode_part(part)
                    chunks.append(_html_to_text(html))
    else:
        content_type = msg.get_content_type()
        body = _decode_part(msg)
        chunks.append(_html_to_text(body) if content_type == "text/html" else body)

    text = "\n".join(chunks)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:MAX_BODY_CHARS]


def _parse_one(msg: Message, index: int) -> dict:
    subject = str(msg.get("subject", "(no subject)"))
    sender = str(msg.get("from", "Unknown sender"))
    date_raw = str(msg.get("date", ""))
    try:
        date = parsedate_to_datetime(date_raw).isoformat() if date_raw else ""
    except (TypeError, ValueError, OverflowError):
        date = date_raw

    message_id = str(msg.get("message-id", "")).strip()
    if not message_id:
        seed = f"{index}|{subject}|{sender}|{date}|{_body_from_message(msg)[:200]}"
        message_id = "generated-" + hashlib.sha256(seed.encode("utf-8", errors="replace")).hexdigest()[:20]

    references = str(msg.get("references", "")).strip()
    in_reply_to = str(msg.get("in-reply-to", "")).strip()
    thread_key = (references.split()[0] if references else in_reply_to or message_id)
    body = _body_from_message(msg)

    return {
        "email_id": message_id.strip("<>"),
        "thread_key": thread_key.strip("<>"),
        "subject": subject[:300],
        "sender": sender[:300],
        "date": date[:100],
        "body": body,
    }


def parse_email_file(path: Path) -> list[dict]:
    suffix = path.suffix.lower()
    parsed: list[dict] = []

    if suffix == ".mbox":
        box = mailbox.mbox(str(path), factory=lambda f: email.message_from_binary_file(f, policy=policy.default))
        messages: Iterable[Message] = list(box)
    elif suffix == ".eml":
        with path.open("rb") as f:
            messages = [email.message_from_binary_file(f, policy=policy.default)]
    else:
        raise ValueError("Unsupported file type. Upload an .mbox or .eml file.")

    for index, msg in enumerate(messages, start=1):
        try:
            parsed.append(_parse_one(msg, index))
        except Exception as exc:
            # One malformed message should not stop the entire import.
            parsed.append({
                "email_id": f"parse-error-{index}",
                "thread_key": f"parse-error-{index}",
                "subject": "(could not parse subject)",
                "sender": "Unknown sender",
                "date": "",
                "body": f"Message could not be parsed: {type(exc).__name__}",
            })
    return parsed
