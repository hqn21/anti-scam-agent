import re

from anti_scam_agent.models import FakePersona
from anti_scam_agent.persona import generate_persona


def test_generate_persona_returns_fake_persona():
    persona = generate_persona()
    assert isinstance(persona, FakePersona)


def test_generate_persona_fields_are_non_empty():
    persona = generate_persona()
    for field_name in FakePersona.model_fields:
        value = getattr(persona, field_name)
        assert value, f"{field_name} was empty: {value!r}"


def test_generate_persona_credit_card_shape():
    persona = generate_persona()
    digits = re.sub(r"\D", "", persona.credit_card_number)
    assert 13 <= len(digits) <= 19, f"unexpected CC length: {digits!r}"
    assert re.fullmatch(r"\d{2}/\d{2,4}", persona.credit_card_expiry), persona.credit_card_expiry
    assert re.fullmatch(r"\d{3,4}", persona.credit_card_cvv), persona.credit_card_cvv


def test_generate_persona_is_not_constant():
    # Fresh persona each call — guards against accidental module-level caching.
    a = generate_persona()
    b = generate_persona()
    assert (a.name, a.email, a.credit_card_number) != (b.name, b.email, b.credit_card_number)
