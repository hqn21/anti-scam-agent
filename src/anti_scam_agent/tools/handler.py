import whois
from typing import Annotated
from pydantic import BaseModel, Field
from datetime import datetime
from zoneinfo import ZoneInfo

_PRIVACY_MARKERS = ("redacted", "privacy", "whoisguard", "domains by proxy", "data protected")


class DomainInfo(BaseModel):
    domain: str
    days_since_creation: Annotated[int, Field(description="The number of days since the creation of the domain.")]
    days_until_expiration: Annotated[int, Field(description="The number of days until the expiration of the domain.")]
    registrar: Annotated[str | None, Field(default=None, description="The domain's registrar, if available.")]
    registrant_country: Annotated[str | None, Field(default=None, description="The registrant's country code, if available.")]
    privacy_protected: Annotated[bool, Field(default=False, description="Whether the registrant identity appears to be privacy-protected / redacted.")]


def _first(value):
    """WHOIS fields are sometimes a list; take the first meaningful entry."""
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


def _looks_privacy_protected(raw: dict) -> bool:
    org = _first(raw.get("org"))
    name = _first(raw.get("name"))
    if org is None and name is None:
        return True
    blob = " ".join(str(v).lower() for v in (org, name) if v is not None)
    return any(marker in blob for marker in _PRIVACY_MARKERS)


def _domain_info_from_whois(raw: dict, domain: str) -> DomainInfo:
    tz = ZoneInfo("Asia/Taipei")
    date_now = datetime.now(tz).date()
    name = str(_first(raw["domain_name"])).lower()
    date_creation = _first(raw["creation_date"]).astimezone(tz).date()
    date_expiration = _first(raw["expiration_date"]).astimezone(tz).date()
    return DomainInfo(
        domain=name,
        days_since_creation=(date_now - date_creation).days,
        days_until_expiration=(date_expiration - date_now).days,
        registrar=_first(raw.get("registrar")),
        registrant_country=_first(raw.get("country")),
        privacy_protected=_looks_privacy_protected(raw),
    )


def _get_domain_info(domain: str) -> DomainInfo:
    return _domain_info_from_whois(whois.whois(domain), domain)
