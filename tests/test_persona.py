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


def _luhn_ok(number: str) -> bool:
    digits = [int(c) for c in re.sub(r"\D", "", number)]
    checksum = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def test_primary_card_is_luhn_invalid():
    persona = generate_persona()
    assert not _luhn_ok(persona.credit_card_number), persona.credit_card_number


def test_fallback_card_is_luhn_valid():
    persona = generate_persona()
    assert _luhn_ok(persona.credit_card_number_luhn_valid), persona.credit_card_number_luhn_valid


def test_phone_has_no_extension():
    persona = generate_persona()
    assert "x" not in persona.phone.lower(), persona.phone


def test_cvv_length_matches_card_type():
    # Amex cards (start with 34 or 37) use a 4-digit CVV; others use 3.
    for _ in range(40):
        persona = generate_persona()
        valid_digits = re.sub(r"\D", "", persona.credit_card_number_luhn_valid)
        is_amex = valid_digits[:2] in {"34", "37"}
        expected = 4 if is_amex else 3
        assert len(persona.credit_card_cvv) == expected, (
            f"{valid_digits[:2]} -> cvv {persona.credit_card_cvv!r}"
        )


def test_name_is_taiwanese_chinese():
    # zh_TW personas have Han-character names, so a real TW site form accepts them.
    persona = generate_persona()
    assert any("一" <= ch <= "鿿" for ch in persona.name), persona.name


def test_phone_is_taiwan_mobile():
    persona = generate_persona()
    assert re.fullmatch(r"09\d{2}-\d{6}", persona.phone), persona.phone


def test_email_is_ascii_example_address():
    # The Chinese name cannot be an email local-part, so the address is a
    # romanized ASCII handle. (Bucket 3 will swap this for an AgentMail inbox.)
    persona = generate_persona()
    assert persona.email.isascii(), persona.email
    assert persona.email.endswith("@example.com"), persona.email


def test_card_type_mix_is_localized():
    # JCB (common in Taiwan) should appear; Discover (rare in TW) should not.
    prefixes = {re.sub(r"\D", "", generate_persona().credit_card_number_luhn_valid)[:2] for _ in range(80)}
    assert "35" in prefixes, f"expected a JCB (prefix 35) in {prefixes}"
    assert "60" not in prefixes and "65" not in prefixes, f"unexpected Discover prefix in {prefixes}"
