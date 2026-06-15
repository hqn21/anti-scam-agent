import datetime

import anti_scam_agent.signals as signals
from anti_scam_agent.signals import (
    DnsInfo,
    StaticSignals,
    TlsInfo,
    _tls_info_from_cert,
    collect_static_signals,
)

_SAMPLE_CERT = {
    "issuer": ((("organizationName", "Let's Encrypt"),), (("commonName", "R3"),)),
    "notBefore": "May 31 21:39:12 2026 GMT",
    "notAfter": "Aug 29 21:41:26 2026 GMT",
    "subjectAltName": (("DNS", "example.com"), ("DNS", "www.example.com")),
}


def test_tls_info_from_cert_parses_fields():
    info = _tls_info_from_cert(_SAMPLE_CERT, now=datetime.datetime(2026, 6, 14, tzinfo=datetime.timezone.utc))
    assert isinstance(info, TlsInfo)
    assert info.issuer_org == "Let's Encrypt"
    assert info.san_count == 2
    assert info.age_days == 14  # 2026-05-31 -> 2026-06-14
    assert info.is_free_dv is True  # Let's Encrypt is a free DV issuer


def test_tls_info_from_cert_flags_non_free_issuer():
    cert = dict(_SAMPLE_CERT, issuer=((("organizationName", "DigiCert Inc"),),))
    info = _tls_info_from_cert(cert, now=datetime.datetime(2026, 6, 14, tzinfo=datetime.timezone.utc))
    assert info.is_free_dv is False


def test_collect_static_signals_is_failure_tolerant(monkeypatch):
    # Every sub-collector raising must still yield a StaticSignals with Nones, never raise.
    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(signals, "_get_domain_info", boom)
    monkeypatch.setattr(signals, "_get_tls_info", boom)
    monkeypatch.setattr(signals, "_get_dns_info", boom)

    result = collect_static_signals("http://nope.invalid")
    assert isinstance(result, StaticSignals)
    assert result.domain_info is None
    assert result.tls is None
    assert result.dns is None


def test_collect_static_signals_bundles_subcollectors(monkeypatch):
    monkeypatch.setattr(signals, "_get_domain_info", lambda d: None)
    monkeypatch.setattr(signals, "_get_tls_info", lambda h: TlsInfo(issuer_org="X", age_days=1, san_count=1, is_free_dv=True))
    monkeypatch.setattr(signals, "_get_dns_info", lambda d: DnsInfo(has_mx=True, nameservers=["ns1.x.com"]))
    result = collect_static_signals("https://shop.example")
    assert result.tls.issuer_org == "X"
    assert result.dns.has_mx is True
    assert result.target_host == "shop.example"


def test_dns_info_absent_records_are_false(monkeypatch):
    import dns.resolver

    from anti_scam_agent.signals import _get_dns_info

    class _Absent:
        lifetime = 0

        def resolve(self, domain, rtype):
            raise dns.resolver.NoAnswer

    monkeypatch.setattr(dns.resolver, "Resolver", lambda: _Absent())
    assert _get_dns_info("x.test").has_mx is False


def test_dns_info_lookup_failure_is_unknown(monkeypatch):
    import dns.resolver

    from anti_scam_agent.signals import _get_dns_info

    class _Down:
        lifetime = 0

        def resolve(self, domain, rtype):
            raise OSError("network down")

    monkeypatch.setattr(dns.resolver, "Resolver", lambda: _Down())
    assert _get_dns_info("x.test").has_mx is None
