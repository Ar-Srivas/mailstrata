"""Rule-based triage: fast, explainable, no AI needed.

Every decision adds a plain-English "signal" so the dashboard can show *why*
something was flagged. Rules run first; Gemma only sees what survives.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from email.utils import parseaddr

PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}


@dataclass
class RuleContext:
    my_email: str = ""
    contacts: Counter = field(default_factory=Counter)  # address -> times you wrote to them
    vip: set[str] = field(default_factory=set)          # addresses or domains
    muted: set[str] = field(default_factory=set)
    keywords: list[str] = field(default_factory=list)


@dataclass
class RuleResult:
    bucket: str = "candidate"          # "noise" | "candidate"
    score: int = 0
    priority: str = "low"
    section: str = "revisit"
    category: str = "general"
    action: str | None = None
    deadline: str | None = None
    forced: bool = False               # VIP or hard pattern: keep even if the AI says "routine"
    signals: list[str] = field(default_factory=list)


# (regex, category, section, priority, why, suggested action)
_HARD = [
    (r"\b(payment failed|card (?:was )?declined|overdue|past due|final notice|unpaid)\b",
     "bill", "deadline", "high", "Looks like an overdue or failed payment",
     "Check this account and pay if it's still open. Use the company's own site, not links in the email."),
    (r"\b(invoice|bill is ready|payment (?:is )?due|amount due|statement is ready|renewal)\b",
     "bill", "deadline", "medium", "Mentions a bill or payment",
     "Check whether this bill has been paid."),
    (r"\b(security alert|suspicious (?:sign[- ]?in|activity)|new sign[- ]?in|unusual activity|password (?:was )?(?:reset|changed))\b",
     "security", "task", "medium", "Security notice about an account",
     "Check that this was you. If not, secure the account from the service's own site."),
    (r"\b(please sign|signature (?:is )?required|docusign|awaiting your (?:signature|approval)|approval (?:needed|required))\b",
     "approval", "waiting", "high", "Waiting for your signature or approval",
     "Review and sign or approve if it's still pending."),
    (r"\b(interview|offer letter|application status)\b",
     "career", "waiting", "medium", "About a job or application",
     "Check whether a reply or confirmation is needed."),
    (r"\b(boarding pass|flight|check-in (?:opens|is open)|booking confirmation|reservation|appointment)\b",
     "event", "revisit", "medium", "Travel, booking or appointment",
     "Check the date and details."),
]
_HARD = [(re.compile(p, re.I), *rest) for p, *rest in _HARD]

_ASK = re.compile(
    r"\b(please (?:send|confirm|review|reply|respond|let me know|advise|approve)|could you|can you|would you|"
    r"need you to|waiting (?:for|on) you|following up|follow-up|just checking in|reminder)\b", re.I)

_DAY = (r"(?:today|tonight|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|eod|eow|"
        r"end of (?:the )?(?:day|week|month)|next week)")
_MON = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_DATE = (rf"(?:\d{{1,2}}(?:st|nd|rd|th)?\s+{_MON}|{_MON}\s+\d{{1,2}}(?:st|nd|rd|th)?|"
         r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}(?:/\d{2,4})?)")
_DEADLINE = re.compile(
    rf"\b(?:by|before|due(?: on| by)?|until|no later than|deadline(?: is)?|on or before)\s+(?:the\s+)?"
    rf"(?:{_DAY}|{_DATE})(?:\s+(?:at\s+)?\d{{1,2}}(?::\d{{2}})?\s*(?:am|pm)?)?", re.I)

_AUTOMATED = re.compile(r"^(no[-_.]?reply|do[-_.]?not[-_.]?reply|mailer-daemon|notifications?|newsletter|updates?|news)\b", re.I)
_BULK_LABELS = {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_FORUMS"}


def _matches(addr: str, values: set[str]) -> bool:
    if not addr or not values:
        return False
    domain = addr.rsplit("@", 1)[-1]
    return addr in values or any(
        "@" not in v and (domain == v or domain.endswith("." + v)) for v in values
    )


def evaluate(email: dict, ctx: RuleContext) -> RuleResult:
    r = RuleResult()
    addr = (email.get("from_addr") or parseaddr(email.get("sender", ""))[1]).lower()
    local = addr.split("@", 1)[0]
    labels = set(email.get("labels", []))
    text = f"{email.get('subject', '')}\n{email.get('body', '')[:2500]}"

    if _matches(addr, ctx.muted):
        r.bucket = "noise"
        r.signals.append("You muted this sender")
        return r

    known = ctx.contacts.get(addr, 0)
    vip = _matches(addr, ctx.vip)
    hits = [h for h in _HARD if h[0].search(text)]

    # Gmail's own promotions/social tabs win, unless you know the sender.
    if labels & _BULK_LABELS and not (known or vip):
        r.bucket = "noise"
        r.signals.append("Gmail files it under promotions or social")
        return r

    mass = []
    if email.get("list_unsubscribe"):
        mass.append("Mass mailing (has an unsubscribe link)")
    if email.get("precedence") in {"bulk", "list", "junk"}:
        mass.append("Sent as bulk mail")
    if email.get("auto_submitted") not in ("", "no", None):
        mass.append("Automatic message")
    if _AUTOMATED.match(local):
        mass.append("Automated sender")
    if mass and not (known or vip or hits):
        r.bucket = "noise"
        r.signals.append(mass[0])
        return r

    # ---- scoring ----
    if vip:
        r.score += 5
        r.forced = True
        r.signals.append("VIP sender (your rule)")
    if known:
        r.score += 3
        r.signals.append(f"You've written to this sender {known} time{'s' if known != 1 else ''}")
    to = (email.get("to") or "")
    if ctx.my_email and ctx.my_email.lower() in to.split():
        r.score += 1
        r.signals.append("Sent directly to you")
    if "UNREAD" in labels:
        r.score += 1
        r.signals.append("You haven't opened it")
    for kw in ctx.keywords:
        if kw and kw in text.lower():
            r.score += 3
            r.signals.append(f"Matches your keyword \"{kw}\"")
            break

    ask = _ASK.search(text)
    if ask:
        r.score += 2
        r.section = "waiting"
        r.signals.append("Asks you to do or answer something")
        r.action = "Check whether this still needs a reply."
    if known and "?" in email.get("body", ""):
        r.score += 1

    m = _DEADLINE.search(text)
    if m:
        r.score += 2
        r.deadline = m.group(0).strip(" .,;")[:120]
        r.section = "deadline"
        r.signals.append(f"Mentions a deadline: {r.deadline}")
        r.action = r.action or "Check whether this deadline still matters."

    best_priority = None
    if hits:
        pattern, cat, section, prio, why, action = min(hits, key=lambda h: PRIORITY_RANK[h[3]])
        r.score += 3
        r.forced = True
        r.category = cat
        r.signals.append(why)
        r.action = action
        if r.section == "revisit" or section != "revisit":
            r.section = section if not r.deadline or section == "deadline" else r.section
        best_priority = prio

    r.priority = "high" if r.score >= 7 else "medium" if r.score >= 4 else "low"
    if vip:
        r.priority = "high"
    if best_priority and PRIORITY_RANK[best_priority] < PRIORITY_RANK[r.priority]:
        r.priority = best_priority
    return r