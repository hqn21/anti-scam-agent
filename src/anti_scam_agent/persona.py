import random

from faker import Faker

from anti_scam_agent.models import FakePersona

_faker = Faker("en_US")


def _email_from_name(name: str) -> str:
    parts = [p.lower() for p in name.split() if p.isalpha()]
    if len(parts) < 2:
        parts = ["user", str(random.randint(1000, 9999))]
    return f"{parts[0]}.{parts[-1]}{random.randint(10, 99)}@example.com"


def generate_persona() -> FakePersona:
    name = _faker.name()
    return FakePersona(
        name=name,
        email=_email_from_name(name),
        password=_faker.password(length=12),
        phone=_faker.phone_number(),
        address=_faker.address().replace("\n", ", "),
        credit_card_number=_faker.credit_card_number(
            card_type=random.choice(["visa", "mastercard", "amex", "discover"])
        ),
        credit_card_expiry=_faker.credit_card_expire(),
        credit_card_cvv=f"{random.randint(0, 999):03d}",
    )
