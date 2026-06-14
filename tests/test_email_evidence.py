from types import SimpleNamespace

import pytest

import anti_scam_agent.email_evidence as ev
from anti_scam_agent.email_evidence import (
    make_client,
    pick_inbox,
    read_inbox_text,
)


def test_pick_inbox_rotates(monkeypatch):
    monkeypatch.setattr(ev, "_get_inboxes", lambda: ["a@x.to", "b@x.to", "c@x.to"])
    ev._reset_inbox_rotation()
    picks = [pick_inbox() for _ in range(4)]
    assert picks == ["a@x.to", "b@x.to", "c@x.to", "a@x.to"]


def test_make_client_raises_without_key(monkeypatch):
    monkeypatch.delenv("AGENTMAIL_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        make_client()


def _msg(from_, text=None, subject="Welcome", message_id="m1"):
    return SimpleNamespace(from_=from_, subject=subject, text=text, message_id=message_id)


def test_read_inbox_text_returns_recent_message_bodies():
    msgs = [_msg("noreply@shop.com", text="Your verification code is 482913")]
    client = SimpleNamespace(
        inboxes=SimpleNamespace(
            messages=SimpleNamespace(list=lambda **kw: SimpleNamespace(messages=msgs))
        )
    )
    out = read_inbox_text(client, "in@x.to")
    assert "482913" in out
    assert "noreply@shop.com" in out


def test_read_inbox_text_empty_inbox():
    client = SimpleNamespace(
        inboxes=SimpleNamespace(
            messages=SimpleNamespace(list=lambda **kw: SimpleNamespace(messages=[]))
        )
    )
    out = read_inbox_text(client, "in@x.to")
    assert "No messages" in out


def test_read_inbox_text_is_failure_tolerant():
    def boom(**kw):
        raise RuntimeError("api down")

    client = SimpleNamespace(
        inboxes=SimpleNamespace(messages=SimpleNamespace(list=boom))
    )
    out = read_inbox_text(client, "in@x.to")
    assert isinstance(out, str) and out  # benign non-empty string, no raise


def test_read_inbox_text_falls_back_to_message_get_for_body():
    # A summary message with no inline body should trigger the full-message fetch.
    light = SimpleNamespace(from_="noreply@shop.com", subject="Code", text=None, message_id="m9")

    class Messages:
        def list(self, **kw):
            return SimpleNamespace(messages=[light])

        def get(self, inbox_id, message_id):
            assert message_id == "m9"
            return SimpleNamespace(text="Your code is 555111")

    client = SimpleNamespace(inboxes=SimpleNamespace(messages=Messages()))
    out = read_inbox_text(client, "in@x.to")
    assert "555111" in out


def test_read_inbox_text_requests_unauthenticated_mail():
    seen = {}

    def fake_list(**kw):
        seen.update(kw)
        return SimpleNamespace(messages=[])

    client = SimpleNamespace(
        inboxes=SimpleNamespace(messages=SimpleNamespace(list=fake_list))
    )
    read_inbox_text(client, "in@x.to")
    assert seen.get("include_unauthenticated") is True
