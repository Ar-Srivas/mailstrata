from collections import Counter

from backend import triage
from backend.rules import RuleContext, evaluate


def mail(i, subject, body, frm="a@x.test", labels=("INBOX", "UNREAD"), thread=None, ts=1, **kw):
    d = dict(email_id=f"id{i}", thread_key=thread or f"t{i}", subject=subject, sender=frm, from_addr=frm,
             date="2026-10-01", body=body, labels=list(labels), internal_ts=ts, to="me@x.test",
             link="https://mail.google.com/mail/u/0/#all/t", list_unsubscribe=False, precedence="", auto_submitted="")
    d.update(kw)
    return d


CTX = lambda **k: RuleContext(my_email="me@x.test", **k)


def test_newsletter_is_noise():
    assert evaluate(mail(1, "Deals!", "50% off", list_unsubscribe=True), CTX()).bucket == "noise"


def test_promotions_label_is_noise_unless_known():
    e = mail(1, "Hi", "x", labels=["CATEGORY_PROMOTIONS"])
    assert evaluate(e, CTX()).bucket == "noise"
    assert evaluate(e, CTX(contacts=Counter({"a@x.test": 2}))).bucket == "candidate"


def test_overdue_bill_is_high_with_deadline_section():
    r = evaluate(mail(1, "Final notice: invoice overdue", "Pay by 5 November.", frm="billing@power.test", list_unsubscribe=True), CTX())
    assert r.bucket == "candidate" and r.priority == "high" and r.section == "deadline"
    assert r.deadline.lower() == "by 5 november"


def test_known_contact_asking_question_scores_up():
    r = evaluate(mail(1, "Quick one", "Could you send the slides by Friday?"), CTX(contacts=Counter({"a@x.test": 4})))
    assert r.score >= 7 and r.priority == "high" and r.section == "deadline"
    assert any("written to this sender" in s for s in r.signals)


def test_mute_and_vip_rules():
    assert evaluate(mail(1, "hi", "x"), CTX(muted={"x.test"})).bucket == "noise"
    r = evaluate(mail(1, "hi", "x"), CTX(vip={"a@x.test"}))
    assert r.priority == "high" and r.forced


def test_generic_application_marketing_is_not_forced_as_career_finding():
    email = mail(
        1,
        "Update: Confirm your application to discover the skills that set you apart",
        "Explore our platform and discover your skills.",
        frm="mailer@certifications.example.test",
        labels=["INBOX", "UNREAD"],
    )

    result = evaluate(email, CTX())

    assert result.forced is False
    assert result.category == "general"


def test_answered_threads_are_dropped_and_rules_only_mode_works(monkeypatch):
    monkeypatch.setattr(triage, "_ollama_up", lambda: False)
    emails = [
        mail(1, "Slides", "Could you send the slides by Friday?", thread="A", ts=1),
        mail(2, "Re: Slides", "Sent!", frm="me@x.test", labels=["SENT"], thread="A", ts=2),
        mail(3, "Contract", "Please sign by 12 Nov.", frm="legal@co.test", thread="B"),
        mail(4, "Sale", "buy", list_unsubscribe=True, thread="C"),
    ]
    items, warnings, stats = triage.run_triage(emails, CTX())
    assert [i["email_id"] for i in items] == ["id3"]
    assert stats["already_answered"] == 1 and stats["from_you"] == 1 and stats["noise"] == 1
    assert any("rules only" in w for w in warnings)
    assert items[0]["reasons"] and items[0]["source"] == "rules"


def test_ai_merge_and_one_finding_per_thread(monkeypatch):
    monkeypatch.setattr(triage, "_ollama_up", lambda: True)

    def fake_analyze(batch, progress_callback=None, model=None):
        out = []
        for e in batch:
            out.append(dict(email_id=e["email_id"], subject=e["subject"], sender=e["sender"], date=e["date"],
                            category="action", section="skip" if e["email_id"] == "id9" else "waiting",
                            priority="low", summary="s", action_required=True, action="Reply", deadline=None,
                            confidence="high", evidence="e"))
        return out, []

    monkeypatch.setattr(triage, "analyze_emails", fake_analyze)
    ctx = CTX(contacts=Counter({"a@x.test": 3}))
    emails = [
        mail(1, "Q", "Can you confirm?", thread="A", ts=1),
        mail(2, "Re: Q", "Can you confirm today?", thread="A", ts=2),
        mail(9, "Meh", "fyi", frm="z@x.test", thread="Z"),
    ]
    items, _, stats = triage.run_triage(emails, ctx)
    assert len([i for i in items if i["thread_key"] == "A"]) == 1
    assert items[0]["source"] == "ai+rules"
    assert items[0]["priority"] in {"high", "medium"}  # rules lift the AI's "low"
    assert all(i["email_id"] != "id9" for i in items)  # AI said routine, rules weak


def test_failed_ai_analysis_does_not_turn_into_rule_fallback_finding(monkeypatch):
    monkeypatch.setattr(triage, "_ollama_up", lambda: True)
    monkeypatch.setattr(triage, "analyze_emails", lambda *_args, **_kwargs: ([], ["One message failed."]))
    emails = [mail(1, "Please confirm", "Could you confirm by Friday?")]

    items, warnings, stats = triage.run_triage(emails, CTX())

    assert items == []
    assert warnings == ["One message failed."]
    assert stats["ai_failed"] == 1