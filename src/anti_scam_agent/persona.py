import random
import re

from faker import Faker

from anti_scam_agent.models import FakePersona

_faker = Faker("en_US")


def _email_from_name(name: str) -> str:
    parts = [p.lower() for p in name.split() if p.isalpha()]
    if len(parts) < 2:
        parts = ["user", str(random.randint(1000, 9999))]
    return f"{parts[0]}.{parts[-1]}{random.randint(10, 99)}@example.com"


def _break_luhn(number: str) -> str:
    """Flip the last (check) digit so the number fails Luhn validation."""
    last = int(number[-1])
    return number[:-1] + str((last + 1) % 10)


def generate_persona() -> FakePersona:
    name = _faker.name()
    card_type = random.choice(["visa", "mastercard", "amex", "discover"])
    valid_card = _faker.credit_card_number(card_type=card_type)
    cvv_len = 4 if card_type == "amex" else 3
    phone = _faker.phone_number().split("x")[0].strip()
    return FakePersona(
        name=name,
        email=_email_from_name(name),
        password=_faker.password(length=12),
        phone=phone,
        address=_faker.address().replace("\n", ", "),
        credit_card_number=_break_luhn(valid_card),
        credit_card_number_luhn_valid=valid_card,
        credit_card_expiry=_faker.credit_card_expire(),
        credit_card_cvv=f"{random.randint(0, 10**cvv_len - 1):0{cvv_len}d}",
    )
