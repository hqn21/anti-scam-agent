import whois
from typing import Annotated
from pydantic import BaseModel, Field
from datetime import datetime
from zoneinfo import ZoneInfo
from agents import function_tool

class DomainInfo(BaseModel):
    domain: str
    days_since_creation: Annotated[int, Field(description="The number of days since the creation of the domain.")]
    days_until_expiration: Annotated[int, Field(description="The number of days until the expiration of the domain.")]

def _get_domain_info(domain: str) -> DomainInfo:
    whois_info = whois.whois(domain)
    domain = whois_info["domain_name"].lower()
    tz = ZoneInfo("Asia/Taipei")
    date_now = datetime.now(tz).date()
    date_creation = whois_info["creation_date"].astimezone(tz).date()
    date_expiration = whois_info["expiration_date"].astimezone(tz).date()
    days_since_creation = (date_now - date_creation).days
    days_until_expiration = (date_expiration - date_now).days
    domain_info = DomainInfo(
        domain=domain,
        days_since_creation=days_since_creation,
        days_until_expiration=days_until_expiration,
    )
    return domain_info

@function_tool
def get_domain_info(
    domain: Annotated[str, "The domain to look up."],
) -> DomainInfo:
    """Fetches domain information such as days since creation and days until expiration."""
    return _get_domain_info(domain)
