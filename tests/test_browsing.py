import asyncio
from types import SimpleNamespace

from anti_scam_agent.browsing import _build_email_tools, _build_task_prompt, _external_links, _fallback_result
from anti_scam_agent.models import FakePersona, Outcome


def _persona() -> FakePersona:
    return FakePersona(
        name="Jane Doe",
        email="jane.doe11@example.com",
        password="hunter2hunter2",
        phone="555-123-4567",
        address="1 Main St, Springfield",
        credit_card_number="4111111111111111",
        credit_card_expiry="08/30",
        credit_card_cvv="123",
    )


def test_prompt_drops_the_no_error_equals_success_instruction():
    prompt = _build_task_prompt("http://example.com", _persona())
    assert "did not respond an explicit error" not in prompt
    assert "account as a success" not in prompt


def test_prompt_typos_are_fixed():
    prompt = _build_task_prompt("http://example.com", _persona())
    assert "when for it fully loaded" not in prompt
    assert "wait for it to fully load" in prompt
    assert "not_attempted" in prompt


def test_prompt_does_not_leak_card_tier_or_luhn():
    prompt = _build_task_prompt("http://example.com", _persona())
    lowered = prompt.lower()
    assert "luhn" not in lowered
    assert "card_tier" not in lowered
    assert "4111111111111111" in prompt  # the card the agent is given


def test_fallback_marks_visit_incomplete():
    result = _fallback_result("http://example.com", "boom")
    assert result.visit_completed is False
    assert result.login_outcome is Outcome.not_attempted
    assert result.payment_outcome is Outcome.not_attempted


def test_external_links_keeps_only_other_hosts():
    urls = [
        "http://shop.test/",
        "http://shop.test/cart",
        "https://www.shop.test/pay",  # same host (www stripped)
        "https://checkout.stripe.com/session",
        None,
        "https://checkout.stripe.com/session",  # duplicate
    ]
    assert _external_links(urls, "http://shop.test") == ["checkout.stripe.com"]


def test_external_links_empty_when_no_navigation():
    assert _external_links([None, "http://shop.test/"], "http://shop.test") == []


def test_external_links_treats_subdomain_as_external():
    urls = ["https://pay.shop.test/checkout"]
    assert _external_links(urls, "http://shop.test") == ["pay.shop.test"]


def _fake_client(code_text):
    msgs = [SimpleNamespace(from_="noreply@shop.com", subject="Code", text=code_text, message_id="m1")]
    return SimpleNamespace(
        inboxes=SimpleNamespace(
            messages=SimpleNamespace(list=lambda **kw: SimpleNamespace(messages=msgs))
        )
    )


def test_email_tool_registers_without_leaking_client_or_inbox():
    tools = _build_email_tools(_fake_client("code 123456"), "in@x.to")
    # tools.registry.registry.actions is browser-use internal API; update if its shape changes.
    action = tools.registry.registry.actions["read_email_inbox"]
    # The LLM-facing schema must expose NO client/inbox params (blind invariant).
    assert list(action.param_model.model_fields.keys()) == []
    desc = action.description.lower()
    # Mirror the full blind-browser forbidden-word list (see CLAUDE.md / test_models).
    for word in ("scam", "phishing", "suspicious", "fake", "fabricated", "fraud", "agentmail", "luhn"):
        assert word not in desc


def test_email_tool_reads_inbox_contents():
    tools = _build_email_tools(_fake_client("Your code is 123456"), "in@x.to")
    action = tools.registry.registry.actions["read_email_inbox"]
    out = asyncio.run(action.function(params=action.param_model()))
    assert "123456" in str(out)
