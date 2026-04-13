from anti_scam_agent.models import BrowsingResult

def test_browsing_result_has_neutral_fields():
    fields = BrowsingResult.model_fields
    # The leaky field must be gone.
    assert "suspicious_observations" not in fields
    # Its neutral replacement must exist.
    assert "unexpected_events" in fields

def test_browsing_result_descriptions_are_neutral():
    leaky_words = {"scam", "phishing", "suspicious", "fake", "fabricated"}
    for name, field in BrowsingResult.model_fields.items():
        desc = (field.description or "").lower()
        for word in leaky_words:
            assert word not in desc, (
                f"Field {name!r} description leaks meta-goal via {word!r}: {desc!r}"
            )
