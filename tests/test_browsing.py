import asyncio
from types import SimpleNamespace

from anti_scam_agent.browsing import (
    _build_email_tools,
    _build_task_prompt,
    _card_was_entered,
    _external_links,
    _salvage_result_from_history,
)
from anti_scam_agent.models import FakePersona, Outcome


def _persona() -> FakePersona:
    return FakePersona(
        name="王怡君",
        email="jane.doe11@example.com",
        password="hunter2hunter2",
        phone="0912-345678",
        address="台北市中山區中山路一段1號",
        credit_card_number="4111111111111111",
        credit_card_expiry="08/30",
        credit_card_cvv="123",
        name_international="Yijun Wang",
        phone_international="+886 912-345678",
        address_international="1 Main St, Springfield, IL 62701",
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


def test_prompt_breaks_out_of_failed_action_loops():
    # General (not case-specific): stop repeating a failing action, re-assess, and
    # either try something different or move on — never loop on a failed action.
    prompt = _build_task_prompt("http://example.com", _persona()).lower()
    assert "keep repeating" in prompt
    assert "two or three times" in prompt
    assert "re-decide" in prompt or "step back" in prompt
    assert "blocked" in prompt
    assert "forward progress" in prompt


def test_prompt_heeds_autoclosed_dialog_messages():
    prompt = _build_task_prompt("http://example.com", _persona())
    low = prompt.lower()
    # the agent is pointed at where the alert text appears and told to obey it
    assert "auto-closed javascript dialogs" in low
    assert "do not repeat" in low


def test_prompt_handles_stale_element_indices():
    low = _build_task_prompt("http://example.com", _persona()).lower()
    assert "no longer available" in low or "page has changed" in low
    assert "currently listed" in low


def test_prompt_offers_text_based_click_fallback():
    # When index-clicking a visible button keeps missing, fall back to find_text /
    # evaluate (JS click by visible text) which bypass the unreliable numbering.
    prompt = _build_task_prompt("http://example.com", _persona())
    assert "find_text" in prompt
    assert "evaluate" in prompt
    assert ".click()" in prompt


def test_prompt_waits_for_lazy_lists_to_settle():
    low = _build_task_prompt("http://example.com", _persona()).lower()
    assert "scroll all the way down" in low
    assert "stops growing" in low


def test_prompt_distinguishes_targeting_miss_from_refusal():
    # A click that didn't land must be retried on the same intended button, not
    # treated as the site refusing the action (so flow-critical buttons aren't abandoned).
    low = _build_task_prompt("http://example.com", _persona()).lower()
    assert "targeting miss" in low
    assert "did not land" in low
    assert "do not abandon" in low


def test_prompt_recovers_from_stray_redirect():
    prompt = _build_task_prompt("http://example.com", _persona()).lower()
    assert "go back" in prompt or "previous page" in prompt
    # tied to being navigated away from the site unexpectedly
    assert "unrelated" in prompt or "different" in prompt


def test_prompt_offers_international_identity():
    prompt = _build_task_prompt("http://example.com", _persona())
    # both the local and the international identity are offered to the agent
    assert "王怡君" in prompt
    assert "Yijun Wang" in prompt
    assert "+886 912-345678" in prompt


class _FakeHistory:
    def __init__(self, actions, urls):
        self._actions = actions
        self._urls = urls

    def model_actions(self):
        return self._actions

    def urls(self):
        return self._urls


def test_card_was_entered_matches_despite_formatting():
    actions = [{"input_text": {"index": 5, "text": "4111-1111 1111 1111"}}]
    assert _card_was_entered(actions, "4111111111111111") is True
    assert _card_was_entered([{"click": {"index": 2}}], "4111111111111111") is False


def test_salvage_zero_evidence_history_marks_incomplete_no_payment():
    # Nothing useful happened — behaves like the old blank fallback.
    result = _salvage_result_from_history(_FakeHistory([], []), "http://example.com", _persona(), "boom")
    assert result.visit_completed is False
    assert result.credit_card_submitted is False
    assert result.payment_outcome is Outcome.not_attempted
    assert result.login_outcome is Outcome.not_attempted
    assert result.unexpected_events == ["boom"]


def test_salvage_preserves_submitted_card_on_hang():
    # The card was entered, then the run stalled/timed out: must NOT be reported as
    # 'no payment attempted'. credit_card_submitted=True + unclear + not declined.
    actions = [{"input_text": {"index": 9, "text": "4111111111111111"}}]
    urls = ["https://shop.test/checkout", "https://pay.unrelated.test/spin"]
    result = _salvage_result_from_history(
        _FakeHistory(actions, urls), "http://shop.test", _persona(), "browsing timed out after 480s"
    )
    assert result.credit_card_submitted is True
    assert result.payment_outcome is Outcome.unclear
    assert result.payment_explicitly_declined is False
    assert result.visit_completed is False
    assert any("stall" in e.lower() or "loading" in e.lower() for e in result.unexpected_events)
    assert "pay.unrelated.test" in result.outgoing_links


def test_salvage_tolerates_broken_history():
    # A history whose accessors raise must still yield a usable result, never raise.
    class Broken:
        def model_actions(self):
            raise RuntimeError("boom")

        def urls(self):
            raise RuntimeError("boom")

    result = _salvage_result_from_history(Broken(), "http://example.com", _persona(), "browsing raised X")
    assert result.visit_completed is False
    assert result.credit_card_submitted is False


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


def test_prompt_instructs_dismissing_blockers():
    prompt = _build_task_prompt("http://example.com", _persona()).lower()
    assert "pop-up" in prompt or "popup" in prompt or "overlay" in prompt
    assert "close" in prompt or "dismiss" in prompt


def test_prompt_prioritizes_completing_the_flow_over_exact_data():
    prompt = _build_task_prompt("http://example.com", _persona()).lower()
    # improvise when the form doesn't fit the persona (distinctive phrasing from step 6)
    assert "make up" in prompt
    assert "plausible" in prompt
    assert "do not get stuck" in prompt or "don't get stuck" in prompt


def test_prompt_prefers_credit_card_payment():
    prompt = _build_task_prompt("http://example.com", _persona()).lower()
    assert "credit card" in prompt


def test_prompt_stays_neutral():
    prompt = _build_task_prompt("http://example.com", _persona()).lower()
    for word in ("scam", "phishing", "suspicious", "fake", "fabricated", "fraud", "anti-"):
        assert word not in prompt
