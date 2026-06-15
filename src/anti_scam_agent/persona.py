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

# Card BIN prefixes chosen so a BIN lookup classifies the number as a CREDIT product,
# not a debit card. credit/debit is a property of the issuer BIN, not the network brand,
# so the network can't decide it on its own; we seed from issuer ranges instead:
#   - amex and jcb are charge/credit networks with essentially no debit products, so they
#     are the most reliable "always credit" choices.
#   - visa and mastercard are mixed networks; the prefixes below are ones commonly listed
#     as credit products. This cannot be verified offline, so if a real BIN lookup still
#     flags one of these as debit, edit or remove that prefix here.
# Card mix stays Taiwan-friendly (JCB present, Discover absent).
# Each entry: (bin_prefixes, total_length, cvv_length).
_CREDIT_CARD_PRODUCTS: dict[str, tuple[list[str], int, int]] = {
    "amex": (["34", "37"], 15, 4),
    "jcb": (["3528", "3540", "3566", "3589"], 16, 3),
    "visa": (["4147", "4313", "4514", "4929"], 16, 3),
    "mastercard": (["5176", "5215", "5425", "5573"], 16, 3),
}


def _luhn_check_digit(body: str) -> str:
    """The single Luhn check digit that makes body + digit a valid card number."""
    checksum = 0
    for i, ch in enumerate(reversed(body)):
        d = int(ch)
        if i % 2 == 0:  # body's rightmost digit sits just left of the (even) check digit
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return str((10 - checksum % 10) % 10)


def _generate_credit_card_number(prefix: str, total_length: int) -> str:
    """A Luhn-valid number of total_length digits that begins with prefix."""
    fill = "".join(str(random.randint(0, 9)) for _ in range(total_length - len(prefix) - 1))
    body = prefix + fill
    return body + _luhn_check_digit(body)


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
    brand = random.choice(list(_CREDIT_CARD_PRODUCTS))
    prefixes, card_len, cvv_len = _CREDIT_CARD_PRODUCTS[brand]
    card_number = _generate_credit_card_number(random.choice(prefixes), card_len)
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
