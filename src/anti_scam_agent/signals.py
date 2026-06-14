import logging
import socket
import ssl
from datetime import datetime, timezone
from urllib.parse import urlparse

from pydantic import BaseModel

from anti_scam_agent.tools.handler import DomainInfo, _get_domain_info

logger = logging.getLogger(__name__)

_TIMEOUT = 8
# Certificate authorities that issue free, domain-validated certs (the near-universal
# choice of throwaway scam sites). This is a property of the cert, not a blacklist.
_FREE_DV_ISSUERS = ("let's encrypt", "zerossl", "google trust services", "buypass", "cloudflare")


class TlsInfo(BaseModel):
    issuer_org: str | None = None
    age_days: int | None = None
    san_count: int | None = None
    is_free_dv: bool | None = None


class DnsInfo(BaseModel):
    has_mx: bool | None = None
    nameservers: list[str] = []


class StaticSignals(BaseModel):
    target_host: str
    domain_info: DomainInfo | None = None
    tls: TlsInfo | None = None
    dns: DnsInfo | None = None


def _target_host(url: str) -> str:
    return (urlparse(url).hostname or "").removeprefix("www.")


def _tls_info_from_cert(cert: dict, now: datetime | None = None) -> TlsInfo:
    now = now or datetime.now(timezone.utc)
    issuer = {k: v for entry in cert.get("issuer", ()) for k, v in entry}
    issuer_org = issuer.get("organizationName")
    not_before = datetime.strptime(cert["notBefore"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
    age_days = (now.date() - not_before.date()).days
    san_count = len([v for k, v in cert.get("subjectAltName", ()) if k == "DNS"])
    is_free_dv = bool(issuer_org) and any(m in issuer_org.lower() for m in _FREE_DV_ISSUERS)
    return TlsInfo(issuer_org=issuer_org, age_days=age_days, san_count=san_count, is_free_dv=is_free_dv)


def _get_tls_info(host: str) -> TlsInfo | None:
    ctx = ssl.create_default_context()
    with socket.create_connection((host, 443), timeout=_TIMEOUT) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as ssock:
            cert = ssock.getpeercert()
    return _tls_info_from_cert(cert)


def _get_dns_info(domain: str) -> DnsInfo:
    import dns.resolver

    resolver = dns.resolver.Resolver()
    resolver.lifetime = _TIMEOUT

    try:
        mx = resolver.resolve(domain, "MX")
        has_mx = len(mx) > 0
    except Exception:
        has_mx = False

    nameservers: list[str] = []
    try:
        ns = resolver.resolve(domain, "NS")
        nameservers = sorted(str(r.target).rstrip(".") for r in ns)
    except Exception:
        nameservers = []

    return DnsInfo(has_mx=has_mx, nameservers=nameservers)


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001 — collectors must never break the pipeline
        logger.warning("static signal %s failed: %s", getattr(fn, "__name__", fn), e)
        return None


def collect_static_signals(url: str) -> StaticSignals:
    """Best-effort local signals. Never raises: any failure degrades to None."""
    host = _target_host(url)
    return StaticSignals(
        target_host=host,
        domain_info=_safe(_get_domain_info, host),
        tls=_safe(_get_tls_info, host),
        dns=_safe(_get_dns_info, host),
    )
