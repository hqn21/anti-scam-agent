import datetime
import json

from anti_scam_agent.tools import DomainInfo, _domain_info_from_whois, _get_domain_info


def test_get_domain_info():
    result = _get_domain_info("haoquan.me")
    print(json.dumps(result.__dict__, indent=2, ensure_ascii=False))
    result = _get_domain_info("example.com")
    print(json.dumps(result.__dict__, indent=2, ensure_ascii=False))


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


def test_domain_info_builder_both_none_is_privacy():
    info = _domain_info_from_whois(_raw(org=None, name=None), "example.com")
    assert info.privacy_protected is True


def test_domain_info_builder_empty_strings_are_privacy():
    info = _domain_info_from_whois(_raw(org="", name=""), "example.com")
    assert info.privacy_protected is True


def test_domain_info_builder_handles_list_dates():
    info = _domain_info_from_whois(
        _raw(
            creation_date=[
                datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc),
                datetime.datetime(2021, 1, 1, tzinfo=datetime.timezone.utc),
            ]
        ),
        "example.com",
    )
    assert info.days_since_creation > 0
