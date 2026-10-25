"""Hybrid triage: rules first, Gemma second, merged into one explainable list.

  1. Drop mail you sent, and threads where you already had the last word.
  2. Rules throw out newsletters, promotions, and automated noise.
  3. Gemma reads the best candidates (most promising first, capped).
  4. Rules and AI are merged. One finding per thread.

If Ollama isn't running, step 3 is skipped and rules alone still produce a brief.
"""
from __future__ import annotations

import os
import urllib.request
from collections import Counter, defaultdict

from backend.recovery_engine import MAX_EMAILS, OLLAMA_URL, _evidence_snippet, analyze_emails
from backend.rules import PRIORITY_RANK, RuleContext, RuleResult, evaluate

MAX_LLM_EMAILS = int(os.getenv("MAX_LLM_EMAILS", str(MAX_EMAILS)))
RULES_ONLY_MIN_SCORE = 4
SECTION_ORDER = {"deadline": 0, "waiting": 1, "task": 2, "decision": 3, "revisit": 4}


def _rank(priority: str) -> int:
    return PRIORITY_RANK.get(priority, 2)


def _ollama_up() -> bool:
    url = os.getenv("OLLAMA_URL", OLLAMA_URL).rstrip("/")
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=2) as resp:
            resp.read()
        return True
    except Exception:
        return False


def rule_item(email: dict, rr: RuleResult) -> dict:
    body = email.get("body", "")
    summary = " ".join(body.split())[:220] or email.get("subject", "")
    return {
        "email_id": email["email_id"],
        "subject": email["subject"],
        "sender": email["sender"],
        "date": email["date"],
        "category": rr.category,
        "section": rr.section,
        "priority": rr.priority,
        "summary": summary,
        "action_required": bool(rr.action),
        "action": rr.action,
        "deadline": rr.deadline,
        "confidence": "low",
        "evidence": _evidence_snippet(body),
        "source": "rules",
    }


def run_triage(emails, ctx: RuleContext, model=None, progress_cb=None, use_llm=True,
               max_llm=MAX_LLM_EMAILS):
    warnings: list[str] = []
    stats: Counter = Counter(fetched=len(emails))

    # 1. Mail you sent, and threads you already answered
    threads = defaultdict(list)
    for e in emails:
        threads[e.get("thread_key") or e["email_id"]].append(e)
    inbound = []
    for msgs in threads.values():
        msgs.sort(key=lambda e: e.get("internal_ts", 0))
        you_last = "SENT" in msgs[-1].get("labels", [])
        for m in msgs:
            if set(m.get("labels", [])) & {"SENT", "DRAFT"}:
                stats["from_you"] += 1
            elif you_last:
                stats["already_answered"] += 1
            else:
                m["_thread_size"] = len(msgs)
                inbound.append(m)

    # 2. Rules
    rules: dict[str, RuleResult] = {}
    candidates = []
    for e in inbound:
        rr = evaluate(e, ctx)
        rules[e["email_id"]] = rr
        if rr.bucket == "noise":
            stats["noise"] += 1
        else:
            candidates.append(e)
    candidates.sort(key=lambda e: -rules[e["email_id"]].score)
    stats["candidates"] = len(candidates)

    # 3. AI on the best candidates
    ai_by_id: dict[str, dict] = {}
    ai_attempted_ids: set[str] = set()
    llm_on = use_llm and bool(candidates)
    if llm_on and not _ollama_up():
        warnings.append("Ollama isn't running, so this brief uses rules only. Start Ollama for deeper analysis.")
        llm_on = False
    if llm_on:
        batch = candidates[:max_llm]
        ai_attempted_ids = {e["email_id"] for e in batch}
        if len(candidates) > len(batch):
            warnings.append(f"The AI reviewed the {len(batch)} most promising of {len(candidates)} candidates; "
                            "the rest were scored by rules only.")
        if progress_cb:
            progress_cb(0, len(batch))
        results, w = analyze_emails(
            batch, progress_callback=progress_cb, model=model,
        )
        warnings += w
        ai_by_id = {r["email_id"]: r for r in results}

    # 4. Merge
    items = []
    for e in candidates:
        rr = rules[e["email_id"]]
        ai = ai_by_id.get(e["email_id"])
        if ai and ai.get("source") != "rules":
            stats["ai_reviewed"] += 1
            if ai["section"] == "skip":
                if not (rr.forced or rr.score >= 6):
                    stats["ai_skipped"] += 1
                    continue
                item = rule_item(e, rr)
            else:
                item = ai
                item["source"] = "ai+rules"
                item["priority"] = min(ai["priority"], rr.priority, key=_rank)
                item["action"] = ai.get("action") or rr.action
                item["deadline"] = ai.get("deadline") or rr.deadline
        else:
            if e["email_id"] in ai_attempted_ids:
                stats["ai_failed"] += 1
                continue
            if not (rr.forced or rr.score >= RULES_ONLY_MIN_SCORE):
                stats["low_signal"] += 1
                continue
            item = ai or rule_item(e, rr)
        if "VIP sender (your rule)" in rr.signals:
            item["priority"] = "high"
        item.update(
            reasons=list(rr.signals), score=rr.score, thread_key=e.get("thread_key", ""),
            thread_size=e.get("_thread_size", 1), link=e.get("link", ""),
            sender_email=e.get("from_addr", ""),
        )
        items.append(item)

    # One finding per thread: keep the strongest
    items.sort(key=lambda i: (_rank(i["priority"]), -i["score"]))
    seen, unique = set(), []
    for i in items:
        key = i["thread_key"] or i["email_id"]
        if key not in seen:
            seen.add(key)
            unique.append(i)
    unique.sort(key=lambda i: (_rank(i["priority"]), SECTION_ORDER.get(i["section"], 5), -i["score"]))
    stats["findings"] = len(unique)

    if len(warnings) > 8:
        warnings = warnings[:8] + [f"…and {len(warnings) - 8} more notes."]
    return unique, warnings, dict(stats)