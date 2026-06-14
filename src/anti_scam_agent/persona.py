import random

from faker import Faker

from anti_scam_agent.models import FakePersona

# The evaluation set targets Taiwanese users, so the persona must look local
# enough that a real TW site's form accepts it (keeping the control signal clean).
_faker = Faker("zh_TW")

# Card mix skewed to Taiwan: JCB is common here, Discover is rare.
_CARD_TYPES = ["visa", "mastercard", "jcb", "amex"]


def _taiwan_mobile() -> str:
    """A clean Taiwanese mobile number (09XX-XXXXXX) that web forms reliably accept."""
    return f"09{random.randint(0, 99):02d}-{random.randint(0, 999999):06d}"


def generate_persona() -> FakePersona:
    name = _faker.name()
    card_type = random.choice(_CARD_TYPES)
    card_number = _faker.credit_card_number(card_type=card_type)
    cvv_len = 4 if card_type == "amex" else 3
    return FakePersona(
        name=name,
        # The Chinese name can't be an email local-part, so use a romanized ASCII
        # handle. The pipeline replaces this with an AgentMail inbox address.
        email=f"{_faker.user_name()}@example.com",
        password=_faker.password(length=12),
        phone=_taiwan_mobile(),
        address=_faker.address().replace("\n", ", "),
        credit_card_number=card_number,
        credit_card_expiry=_faker.credit_card_expire(),
        credit_card_cvv=f"{random.randint(0, 10**cvv_len - 1):0{cvv_len}d}",
    )
