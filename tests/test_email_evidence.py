import datetime
from types import SimpleNamespace

import anti_scam_agent.email_evidence as ev
from anti_scam_agent.email_evidence import (
    EmailEvidence,
    _domain_matches,
    _evidence_from_messages,
    _is_authenticated,
    _sender_domain,
    collect_email_evidence,
    pick_inbox,
)


def _msg(from_, labels=(), subject="Welcome", when=None):
    return SimpleNamespace(
        from_=from_,
        labels=list(labels),
        subject=subject,
        timestamp=when or datetime.datetime(2026, 6, 14, 12, 0, tzinfo=datetime.timezone.utc),
    )


def test_sender_domain_extraction():
    assert _sender_domain("No Reply <noreply@mail.Shop.com>") == "mail.shop.com"
    assert _sender_domain("plain@shop.com") == "shop.com"
    assert _sender_domain("garbage") == ""


def test_domain_matches_exact_and_subdomain():
    assert _domain_matches("shop.com", "shop.com") is True
    assert _domain_matches("mail.shop.com", "shop.com") is True
    assert _domain_matches("shop.com", "checkout.shop.com") is True
    assert _domain_matches("evil.com", "shop.com") is False


def test_is_authenticated_uses_unauthenticated_label():
    assert _is_authenticated(["inbound"]) is True
    assert _is_authenticated(["inbound", "unauthenticated"]) is False


def test_evidence_from_messages_strong_case():
    msgs = [_msg("noreply@shop.com", labels=["inbound"])]
    e = _evidence_from_messages(msgs, "shop.com")
    assert e.polled is True
    assert e.message_count == 1
    assert e.from_target_domain is True
    assert e.authenticated is True


def test_evidence_from_messages_unauthenticated_domain_mail():
    msgs = [_msg("noreply@shop.com", labels=["inbound", "unauthenticated"])]
    e = _evidence_from_messages(msgs, "shop.com")
    assert e.from_target_domain is True
    assert e.authenticated is False


def test_evidence_from_messages_unrelated_only():
    msgs = [_msg("promo@randomcdn.biz", labels=["inbound"])]
    e = _evidence_from_messages(msgs, "shop.com")
    assert e.message_count == 1
    assert e.from_target_domain is False
    assert e.authenticated is None


def test_pick_inbox_rotates(monkeypatch):
    monkeypatch.setattr(ev, "_get_inboxes", lambda: ["a@x.to", "b@x.to", "c@x.to"])
    ev._reset_inbox_rotation()
    picks = [pick_inbox() for _ in range(4)]
    assert picks == ["a@x.to", "b@x.to", "c@x.to", "a@x.to"]


def test_collect_email_evidence_early_exits_on_match():
    since = datetime.datetime(2026, 6, 14, 11, 0, tzinfo=datetime.timezone.utc)
    calls = {"n": 0}

    class FakeMessages:
        def list(self, **kwargs):
            calls["n"] += 1
            if calls["n"] >= 2:
                return SimpleNamespace(count=1, messages=[_msg("noreply@shop.com", labels=["inbound"])])
            return SimpleNamespace(count=0, messages=[])

    client = SimpleNamespace(inboxes=SimpleNamespace(messages=FakeMessages()))
    e = collect_email_evidence(client, "in@x.to", "shop.com", since, poll_seconds=60, interval=0)
    assert e.from_target_domain is True
    assert calls["n"] == 2


def test_collect_email_evidence_failure_tolerant():
    class Boom:
        def list(self, **kwargs):
            raise RuntimeError("api down")

    client = SimpleNamespace(inboxes=SimpleNamespace(messages=Boom()))
    e = collect_email_evidence(client, "in@x.to", "shop.com",
                               datetime.datetime.now(datetime.timezone.utc), poll_seconds=0, interval=0)
    assert isinstance(e, EmailEvidence)
    assert e.polled is False


def test_collect_email_evidence_keeps_last_on_later_failure():
    since = datetime.datetime(2026, 6, 14, 11, 0, tzinfo=datetime.timezone.utc)
    calls = {"n": 0}

    class Flaky:
        def list(self, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return SimpleNamespace(
                    count=2,
                    messages=[_msg("promo@randomcdn.biz", labels=["inbound"]),
                              _msg("x@other.biz", labels=["inbound"])],
                )
            raise RuntimeError("transient")

    client = SimpleNamespace(inboxes=SimpleNamespace(messages=Flaky()))
    e = collect_email_evidence(client, "in@x.to", "shop.com", since, poll_seconds=30, interval=0)
    assert e.polled is True            # first poll succeeded
    assert e.message_count == 2        # its result is preserved despite the later failure
    assert e.from_target_domain is False
