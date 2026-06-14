from anti_scam_agent.models import BrowsingResult, Outcome

def test_browsing_result_has_neutral_fields():
    fields = BrowsingResult.model_fields
    # The leaky field must be gone.
    assert "suspicious_observations" not in fields
    # Its neutral replacement must exist.
    assert "unexpected_events" in fields

def test_browsing_result_descriptions_are_neutral():
    leaky_words = {"scam", "phishing", "suspicious", "fake", "fabricated", "luhn", "card_tier"}
    for name, field in BrowsingResult.model_fields.items():
        desc = (field.description or "").lower()
        for word in leaky_words:
            assert word not in desc, (
                f"Field {name!r} description leaks meta-goal via {word!r}: {desc!r}"
            )


def test_outcome_enum_values_are_neutral():
    leaky_words = {"scam", "phishing", "suspicious", "fake", "fabricated"}
    for member in Outcome:
        assert member.value not in leaky_words
    assert {m.value for m in Outcome} == {
        "not_attempted",
        "failed",
        "unclear",
        "succeeded",
    }


def test_browsing_result_uses_four_state_outcomes():
    fields = BrowsingResult.model_fields
    # Old leaky-by-coupling booleans are gone.
    assert "login_succeeded" not in fields
    assert "credit_card_accepted" not in fields
    # New four-state fields exist with the Outcome type.
    assert fields["login_outcome"].annotation is Outcome
    assert fields["payment_outcome"].annotation is Outcome
    # Abstain flag for the fallback path.
    assert "visit_completed" in fields
    # The "was it tried at all" booleans are retained.
    assert "login_attempted" in fields
    assert "credit_card_submitted" in fields
