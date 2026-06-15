import re

from anti_scam_agent.models import CreditCard, FakePersona
from anti_scam_agent.persona import _CREDIT_CARD_PRODUCTS, generate_persona


def test_generate_persona_returns_fake_persona():
    assert isinstance(generate_persona(), FakePersona)


def test_generate_persona_fields_are_non_empty():
    persona = generate_persona()
    for field_name in FakePersona.model_fields:
        value = getattr(persona, field_name)
        assert value, f"{field_name} was empty: {value!r}"


def test_persona_has_one_card_per_configured_brand():
    persona = generate_persona()
    assert len(persona.cards) == len(_CREDIT_CARD_PRODUCTS)
    assert all(isinstance(c, CreditCard) for c in persona.cards)


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


def test_every_card_is_luhn_valid():
    for card in generate_persona().cards:
        assert _luhn_ok(card.number), card.number


def test_card_shapes_are_valid():
    for card in generate_persona().cards:
        digits = re.sub(r"\D", "", card.number)
        assert 13 <= len(digits) <= 19, f"unexpected CC length: {digits!r}"
        assert re.fullmatch(r"\d{2}/\d{2,4}", card.expiry), card.expiry
        assert re.fullmatch(r"\d{3,4}", card.cvv), card.cvv


def test_cvv_length_matches_card_type():
    # Amex cards (start with 34 or 37) use a 4-digit CVV; others use 3.
    for _ in range(40):
        for card in generate_persona().cards:
            digits = re.sub(r"\D", "", card.number)
            is_amex = digits[:2] in {"34", "37"}
            expected = 4 if is_amex else 3
            assert len(card.cvv) == expected, f"{digits[:2]} -> cvv {card.cvv!r}"


def test_cards_use_credit_bin_prefixes():
    # Every number must begin with one of the curated credit-product BIN prefixes
    # (so it is classified as a credit card, not debit), never a random brand prefix.
    allowed = tuple(p for prefixes, _, _ in _CREDIT_CARD_PRODUCTS.values() for p in prefixes)
    for _ in range(40):
        for card in generate_persona().cards:
            digits = re.sub(r"\D", "", card.number)
            assert digits.startswith(allowed), digits


def test_no_discover_brand():
    # Discover (60/65) is intentionally excluded from the credit-BIN set.
    prefixes = {
        re.sub(r"\D", "", card.number)[:2]
        for _ in range(40)
        for card in generate_persona().cards
    }
    assert "60" not in prefixes and "65" not in prefixes, prefixes


def test_generate_persona_is_not_constant():
    # Fresh persona each call — guards against accidental module-level caching.
    a = generate_persona()
    b = generate_persona()
    assert (a.name, a.email, a.cards[0].number) != (b.name, b.email, b.cards[0].number)


def test_phone_has_no_extension():
    persona = generate_persona()
    assert "x" not in persona.phone.lower(), persona.phone


def test_email_is_ascii_example_address():
    # The pipeline swaps this for an AgentMail inbox; here it is an ASCII handle.
    persona = generate_persona()
    assert persona.email.isascii(), persona.email
    assert persona.email.endswith("@example.com"), persona.email


def test_name_is_ascii_english():
    persona = generate_persona()
    assert persona.name.isascii(), persona.name
    assert persona.name.strip(), persona.name


def test_address_is_ascii_single_line():
    persona = generate_persona()
    assert persona.address.isascii(), persona.address
    assert "\n" not in persona.address, persona.address
