"""Read-only Gmail access.

Safety design:
  * Only the gmail.readonly scope is ever requested. It cannot send, delete or modify mail.
  * Tokens live in data/gmail_token.json (owner-only permissions) and never leave this computer.
  * "Disconnect" revokes the token at Google and deletes the local file.
"""
from __future__ import annotations

import base64
import os
import threading
import time
import urllib.parse
import urllib.request
from collections import Counter
from email import message_from_bytes, policy
from email.utils import getaddresses, parseaddr
from pathlib import Path

from backend.email_parser import _parse_one

try:
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import Flow
    from googleapiclient.discovery import build
    GOOGLE_OK = True
except ImportError:  # app still runs for file uploads
    GOOGLE_OK = False

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


class GmailAuth:
    def __init__(self, data_dir: Path, redirect_uri: str):
        self.creds_path = data_dir / "credentials.json"
        self.token_path = data_dir / "gmail_token.json"
        self.redirect_uri = redirect_uri
        self._pending: dict[str, "Flow"] = {}
        self._lock = threading.Lock()
        if redirect_uri.startswith(("http://127.0.0.1", "http://localhost")):
            os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")  # loopback only

    def configured(self) -> bool:
        return GOOGLE_OK and self.creds_path.exists()

    def credentials(self):
        if not GOOGLE_OK or not self.token_path.exists():
            return None
        try:
            creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)
            if not creds.valid and creds.refresh_token:
                creds.refresh(Request())
                self._save(creds)
            return creds if creds.valid else None
        except (RefreshError, ValueError):
            self.token_path.unlink(missing_ok=True)
            return None

    def start(self) -> str:
        flow = Flow.from_client_secrets_file(str(self.creds_path), scopes=SCOPES, redirect_uri=self.redirect_uri)
        url, state = flow.authorization_url(access_type="offline", prompt="consent")
        with self._lock:
            self._pending = dict(list(self._pending.items())[-4:])  # keep it small
            self._pending[state] = flow
        return url

    def finish(self, state: str, code: str):
        with self._lock:
            flow = self._pending.pop(state, None)
        if flow is None:
            raise ValueError("The sign-in session expired. Click Connect Gmail again.")
        flow.fetch_token(code=code)
        self._save(flow.credentials)
        return flow.credentials

    def disconnect(self) -> None:
        creds = self.credentials()
        token = getattr(creds, "token", None)
        if token:
            try:
                data = urllib.parse.urlencode({"token": token}).encode()
                urllib.request.urlopen(urllib.request.Request("https://oauth2.googleapis.com/revoke", data=data), timeout=8)
            except Exception:
                pass  # still delete the local copy
        self.token_path.unlink(missing_ok=True)

    def _save(self, creds) -> None:
        self.token_path.write_text(creds.to_json())
        try:
            os.chmod(self.token_path, 0o600)
        except OSError:
            pass


def _service(creds):
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def account_email(creds) -> str:
    return _service(creds).users().getProfile(userId="me").execute().get("emailAddress", "")


def _list_ids(svc, query: str, limit: int) -> list[str]:
    ids: list[str] = []
    req = svc.users().messages().list(userId="me", q=query, maxResults=min(500, limit))
    while req is not None and len(ids) < limit:
        resp = req.execute()
        ids += [m["id"] for m in resp.get("messages", [])]
        req = svc.users().messages().list_next(req, resp)
    return ids[:limit]


def _get_many(svc, ids, fmt, progress=None, headers=None):
    got, failed = {}, []

    def cb(request_id, response, exception):
        (failed.append(request_id) if exception else got.__setitem__(request_id, response))

    def one(mid):
        kw = {"userId": "me", "id": mid, "format": fmt}
        if headers:
            kw["metadataHeaders"] = headers
        return svc.users().messages().get(**kw)

    for i in range(0, len(ids), 25):
        batch = svc.new_batch_http_request(callback=cb)
        for mid in ids[i:i + 25]:
            batch.add(one(mid), request_id=mid)
        batch.execute()
        if progress:
            progress(len(got) + len(failed), len(ids))
        time.sleep(0.2)  # be gentle with rate limits
    retry, failed[:] = failed[:], []
    for mid in retry:  # one slow retry pass
        try:
            got[mid] = one(mid).execute()
        except Exception:
            failed.append(mid)
    return got, failed


def _to_email(resp: dict, index: int) -> dict:
    raw = resp["raw"]
    msg = message_from_bytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)), policy=policy.default)
    d = _parse_one(msg, index)
    tid = resp.get("threadId", "")
    d.update(
        thread_key="gm-" + tid,
        gmail_id=resp["id"],
        labels=resp.get("labelIds", []),
        internal_ts=int(resp.get("internalDate", "0")) / 1000,
        from_addr=parseaddr(str(msg.get("from", "")))[1].lower(),
        to=" ".join(a.lower() for _, a in getaddresses([str(msg.get("to", ""))]) if a),
        list_unsubscribe=bool(msg.get("list-unsubscribe")),
        precedence=str(msg.get("precedence", "")).strip().lower(),
        auto_submitted=str(msg.get("auto-submitted", "")).strip().lower(),
        link=f"https://mail.google.com/mail/u/0/#all/{tid}",
    )
    return d


def _sent_contacts(svc, cap: int = 300) -> Counter:
    """Who do you actually write to? Strong signal that a sender matters."""
    ids = _list_ids(svc, "in:sent newer_than:180d", cap)
    got, _ = _get_many(svc, ids, "metadata", headers=["To", "Cc"])
    counts: Counter = Counter()
    for resp in got.values():
        for h in resp.get("payload", {}).get("headers", []):
            if h.get("name", "").lower() in ("to", "cc"):
                counts.update(a.lower() for _, a in getaddresses([h["value"]]) if a)
    return counts


def fetch_inbox(creds, days: int, max_emails: int, progress=None):
    svc = _service(creds)
    me = svc.users().getProfile(userId="me").execute().get("emailAddress", "")
    ids = _list_ids(svc, f"newer_than:{days}d", max_emails)
    got, failed = _get_many(svc, ids, "raw", progress)
    emails = [_to_email(resp, i) for i, resp in enumerate(got.values(), start=1)]
    warnings = []
    if failed:
        warnings.append(f"{len(failed)} messages couldn't be downloaded and were skipped.")
    try:
        contacts = _sent_contacts(svc)
    except Exception as exc:
        contacts = Counter()
        warnings.append(f"Couldn't read your sent mail to learn who matters to you: {exc}")
    return emails, contacts, me, warnings