from anti_scam_agent.tools import *
import json

def test_get_domain_info():
    result = _get_domain_info("haoquan.me")
    print(json.dumps(result.__dict__, indent=2, ensure_ascii=False))
    result = _get_domain_info("example.com")
    print(json.dumps(result.__dict__, indent=2, ensure_ascii=False))


import datetime

from anti_scam_agent.tools.handler import _domain_info_from_whois, DomainInfo


def _raw(**overrides):
    base = {
        "domain_name": "EXAMPLE.COM",
        "creation_date": datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc),
        "expiration_date": datetime.datetime(2030, 1, 1, tzinfo=datetime.timezone.utc),
        "registrar": "Example Registrar, Inc.",
        "country": "US",
        "org": "Example Org",
        "name": "Jane Doe",
    }
    base.update(overrides)
    return base


def test_domain_info_builder_basic_fields():
    info = _domain_info_from_whois(_raw(), "example.com")
    assert isinstance(info, DomainInfo)
    assert info.domain == "example.com"
    assert info.days_since_creation > 0
    assert info.days_until_expiration > 0
    assert info.registrar == "Example Registrar, Inc."
    assert info.registrant_country == "US"
    assert info.privacy_protected is False


def test_domain_info_builder_detects_privacy():
    info = _domain_info_from_whois(_raw(org=None, name="REDACTED FOR PRIVACY"), "example.com")
    assert info.privacy_protected is True


def test_domain_info_builder_handles_list_domain_name():
    info = _domain_info_from_whois(_raw(domain_name=["EXAMPLE.COM", "example.com"]), "example.com")
    assert info.domain == "example.com"