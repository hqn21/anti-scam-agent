import random

from faker import Faker

from anti_scam_agent.models import CreditCard, FakePersona

# A single English-language identity (name / address / phone), for the foreign,
# English-facing sites this is run against.
_faker = Faker("en_US")

# Card BIN prefixes chosen so a BIN lookup classifies the number as a CREDIT product,
# not a debit card. credit/debit is a property of the issuer BIN, not the network brand,
# so the network can't decide it on its own; we seed from issuer ranges instead. These
# cannot be verified offline, so if a real BIN lookup still flags one of these as debit,
# edit or remove that prefix here. The persona carries one card per brand below so the
# browser can fall back to another if a site refuses a given card by type.
# Each entry: (bin_prefixes, total_length, cvv_length).
_CREDIT_CARD_PRODUCTS: dict[str, tuple[list[str], int, int]] = {
    "visa": (["4147", "4313", "4514", "4929"], 16, 3),
    "mastercard": (["5176", "5215", "5425", "5573"], 16, 3),
    "amex": (["34", "37"], 15, 4),
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


def _generate_card(brand: str) -> CreditCard:
    """A Luhn-valid CreditCard for one of the brands in _CREDIT_CARD_PRODUCTS."""
    prefixes, card_len, cvv_len = _CREDIT_CARD_PRODUCTS[brand]
    return CreditCard(
        number=_generate_credit_card_number(random.choice(prefixes), card_len),
        expiry=_faker.credit_card_expire(),
        cvv=f"{random.randint(0, 10**cvv_len - 1):0{cvv_len}d}",
    )


def _us_phone() -> str:
    """A clean US phone number (no extension) that web forms reliably accept."""
    return f"({random.randint(200, 989)}) {random.randint(200, 999)}-{random.randint(0, 9999):04d}"


def generate_persona() -> FakePersona:
    return FakePersona(
        name=f"{_faker.first_name()} {_faker.last_name()}",
        # The pipeline replaces this with an AgentMail inbox address before browsing.
        email=f"{_faker.user_name()}@example.com",
        password=_faker.password(length=12),
        phone=_us_phone(),
        address=_faker.address().replace("\n", ", "),
        # One card per brand, so the browser can try another if a site refuses one by type.
        cards=[_generate_card(brand) for brand in _CREDIT_CARD_PRODUCTS],
    )
