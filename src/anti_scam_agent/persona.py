import random

from faker import Faker
from pypinyin import lazy_pinyin

from anti_scam_agent.models import FakePersona

# The evaluation set targets Taiwanese users, so the persona must look local
# enough that a real TW site's form accepts it (keeping the control signal clean).
_faker = Faker("zh_TW")
# A Latin-script counterpart for the persona's international-facing details
# (foreign / English-only forms that won't accept Chinese characters).
_faker_intl = Faker("en_US")

# Card mix skewed to Taiwan: JCB is common here, Discover is rare.
_CARD_TYPES = ["visa", "mastercard", "jcb", "amex"]


def _taiwan_mobile() -> str:
    """A clean Taiwanese mobile number (09XX-XXXXXX) that web forms reliably accept."""
    return f"09{random.randint(0, 99):02d}-{random.randint(0, 999999):06d}"


def _romanize_name(name: str) -> str:
    """Passport-style romanization of a Chinese name, in Western 'Given Surname' order.

    Treats the first character as the surname (covers the vast majority of TW names;
    rare two-character surnames are romanized as a single token, which forms accept).
    """
    syllables = lazy_pinyin(name)
    if not syllables:
        return name
    surname = syllables[0].capitalize()
    given = "".join(syllables[1:]).capitalize()
    return f"{given} {surname}" if given else surname


def _international_phone(mobile: str) -> str:
    """Convert a local 09XX-XXXXXX mobile to +886 international format (drop the leading 0)."""
    national = mobile[1:] if mobile.startswith("0") else mobile
    return f"+886 {national}"


def generate_persona() -> FakePersona:
    name = _faker.name()
    phone = _taiwan_mobile()
    card_type = random.choice(_CARD_TYPES)
    card_number = _faker.credit_card_number(card_type=card_type)
    cvv_len = 4 if card_type == "amex" else 3
    return FakePersona(
        name=name,
        # The Chinese name can't be an email local-part, so use a romanized ASCII
        # handle. The pipeline replaces this with an AgentMail inbox address.
        email=f"{_faker.user_name()}@example.com",
        password=_faker.password(length=12),
        phone=phone,
        address=_faker.address().replace("\n", ", "),
        credit_card_number=card_number,
        credit_card_expiry=_faker.credit_card_expire(),
        credit_card_cvv=f"{random.randint(0, 10**cvv_len - 1):0{cvv_len}d}",
        # Same person, international-facing form (email/password/card are already
        # international, so only name/phone/address need a Latin-script variant).
        name_international=_romanize_name(name),
        phone_international=_international_phone(phone),
        address_international=_faker_intl.address().replace("\n", ", "),
    )
