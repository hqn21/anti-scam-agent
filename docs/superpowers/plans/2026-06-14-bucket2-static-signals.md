# Bucket 2 — Cheap Out-of-Band Signals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add cheap, fully-local signals (TLS certificate, DNS/MX, expanded WHOIS) computed in the pipeline and fed to the Analysis Agent as structured input, plus replace LLM-self-reported `outgoing_links` with the real visited-URL trail — all without blacklists.

**Architecture:** A new failure-tolerant `collect_static_signals(url)` bundles an expanded `DomainInfo` + `TlsInfo` + `DnsInfo` into a `StaticSignals` model. The pipeline computes it once (off the event loop) and passes it to `run_analysis_agent` as input; the `get_domain_info` LLM tool is removed (the data is now provided directly, avoiding a round-trip and a duplicate WHOIS call). `outgoing_links` is derived programmatically from `browser-use`'s `history.urls()`. Every collector degrades to `None`/empty on any failure so the analysis stage is never skipped.

**Tech Stack:** Python 3.12, Pydantic v2, stdlib `ssl`/`socket`, `dnspython` (new dep), `python-whois`, `browser-use`, `openai-agents`, pytest, `uv`.

**Design decisions (locked):**
- Static signals delivered as **analysis input**; `get_domain_info` removed as an agent tool (the `_get_domain_info` impl is retained and reused by the collector).
- **`dnspython`** added for MX/nameserver lookups.
- **Hosting ASN dropped** from the original spec — IP→ASN mapping needs an external database/service, which breaks the local-only / no-blacklist principle and adds infra. MX presence + nameservers cover the "is this domain really operating mail" signal locally.
- `outgoing_links` from `history.urls()` (dedupe, drop `None`, keep only hosts different from the target).

---

## File Structure

- `pyproject.toml` — add `dnspython` to dependencies.
- `src/anti_scam_agent/tools/handler.py` — expand `DomainInfo` (registrar, registrant_country, privacy_protected); refactor `_get_domain_info` to a pure builder `_domain_info_from_whois(raw, domain)` for offline testability; **remove** the `@function_tool get_domain_info` wrapper.
- `src/anti_scam_agent/tools/__init__.py` — stop re-exporting `get_domain_info`; export `_get_domain_info`/`DomainInfo`.
- `src/anti_scam_agent/signals.py` (new) — `TlsInfo`, `DnsInfo`, `StaticSignals` models; pure parsers; failure-tolerant `_get_tls_info`/`_get_dns_info`; `collect_static_signals(url)`.
- `src/anti_scam_agent/browsing.py` — `_external_links(urls, target_url)` pure helper; populate `outgoing_links` from `history.urls()`.
- `src/anti_scam_agent/pipeline.py` — compute static signals via `asyncio.to_thread`, pass to analysis.
- `src/anti_scam_agent/analysis.py` — `run_analysis_agent` gains `static_signals`; drop the tool; prompt reads signals from input and adds TLS/DNS/WHOIS heuristics.
- `CLAUDE.md` — update the tools-convention note now that the only tool is removed.
- Tests: `tests/test_signals.py` (new, offline), `tests/test_tools.py` (add offline builder test), `tests/test_browsing.py` (add `_external_links` tests), `tests/test_pipeline.py` (patch the collector), `tests/test_analysis.py` (signature + fixture call).

---

### Task 1: Add `dnspython`; expand `DomainInfo` with an offline-testable builder

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/anti_scam_agent/tools/handler.py`
- Modify: `src/anti_scam_agent/tools/__init__.py`
- Test: `tests/test_tools.py`

- [ ] **Step 1: Add the dependency**

Run: `uv add dnspython`
Expected: `pyproject.toml` gains `dnspython` under `dependencies`; lockfile updates. Verify import: `uv run python -c "import dns.resolver; print('ok')"` → `ok`.

- [ ] **Step 2: Write the failing offline test**

Add to `tests/test_tools.py`:

```python
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
```

Run: `uv run pytest tests/test_tools.py -k builder -v`
Expected: FAIL — `_domain_info_from_whois` does not exist.

- [ ] **Step 3: Expand the model and refactor to a pure builder**

Replace the contents of `src/anti_scam_agent/tools/handler.py` with:

```python
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
```

(Note: `_first` is now applied to the dates too, hardening the pre-existing assumption that `domain_name`/dates are scalars. The `@function_tool get_domain_info` wrapper is intentionally removed — Task 5 removes its last use in `analysis.py`.)

- [ ] **Step 4: Update the package exports**

Replace `src/anti_scam_agent/tools/__init__.py` with:

```python
from anti_scam_agent.tools.handler import DomainInfo, _domain_info_from_whois, _get_domain_info

__all__ = ["DomainInfo", "_domain_info_from_whois", "_get_domain_info"]
```

(If the existing file re-exports `get_domain_info`, removing it is required — that symbol no longer exists.)

- [ ] **Step 5: Run the offline builder tests**

Run: `uv run pytest tests/test_tools.py -k builder -v`
Expected: PASS (3 tests). The pre-existing live test `test_get_domain_info` still calls `_get_domain_info` and remains a network test.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/anti_scam_agent/tools/handler.py src/anti_scam_agent/tools/__init__.py tests/test_tools.py
git commit -m "feat: expand DomainInfo (registrar/country/privacy) with offline builder; add dnspython

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `signals.py` — TLS + DNS collectors and the `StaticSignals` bundle

**Files:**
- Create: `src/anti_scam_agent/signals.py`
- Test: `tests/test_signals.py` (create)

- [ ] **Step 1: Write the failing offline tests**

Create `tests/test_signals.py`:

```python
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
```

Run: `uv run pytest tests/test_signals.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 2: Implement `signals.py`**

Create `src/anti_scam_agent/signals.py`:

```python
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
    age_days = (now - not_before).days
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
```

- [ ] **Step 3: Run the offline tests**

Run: `uv run pytest tests/test_signals.py -v`
Expected: PASS (4 tests).

- [ ] **Step 4: Commit**

```bash
git add src/anti_scam_agent/signals.py tests/test_signals.py
git commit -m "feat: TLS/DNS static-signal collectors with failure-tolerant bundle

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Programmatic `outgoing_links` from the browser history

**Files:**
- Modify: `src/anti_scam_agent/browsing.py`
- Test: `tests/test_browsing.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_browsing.py`:

```python
from anti_scam_agent.browsing import _external_links


def test_external_links_keeps_only_other_hosts():
    urls = [
        "http://shop.test/",
        "http://shop.test/cart",
        "https://www.shop.test/pay",  # same host (www stripped)
        "https://checkout.stripe.com/session",
        None,
        "https://checkout.stripe.com/session",  # duplicate
    ]
    assert _external_links(urls, "http://shop.test") == ["checkout.stripe.com"]


def test_external_links_empty_when_no_navigation():
    assert _external_links([None, "http://shop.test/"], "http://shop.test") == []
```

Run: `uv run pytest tests/test_browsing.py -k external -v`
Expected: FAIL — `_external_links` does not exist.

- [ ] **Step 2: Implement the helper and wire it in**

In `src/anti_scam_agent/browsing.py`, add the import near the top:

```python
from urllib.parse import urlparse
```

Add this pure helper (place it above `run_browsing_agent`):

```python
def _external_links(urls: list[str | None], target_url: str) -> list[str]:
    """Distinct hosts visited during the run that differ from the target host."""
    target = (urlparse(target_url).hostname or "").removeprefix("www.")
    seen: list[str] = []
    for url in urls:
        if not url:
            continue
        host = (urlparse(url).hostname or "").removeprefix("www.")
        if host and host != target and host not in seen:
            seen.append(host)
    return seen
```

In `run_browsing_agent`, after a `BrowsingResult` is obtained from the successful path, override its `outgoing_links` with the real trail. Change the success branch so that both the `isinstance` and the parsed-`dict` cases run through a small finalizer. Concretely, replace:

```python
    structured = history.structured_output
    if isinstance(structured, BrowsingResult):
        return structured
    if isinstance(structured, dict):
        try:
            return BrowsingResult.model_validate(structured)
        except Exception as e:
            logger.warning("failed to parse structured dict on %s: %s", url, e)
            return _fallback_result(url, f"parsing structured output failed: {e}")
```

with:

```python
    structured = history.structured_output
    result: BrowsingResult | None = None
    if isinstance(structured, BrowsingResult):
        result = structured
    elif isinstance(structured, dict):
        try:
            result = BrowsingResult.model_validate(structured)
        except Exception as e:
            logger.warning("failed to parse structured dict on %s: %s", url, e)
            return _fallback_result(url, f"parsing structured output failed: {e}")

    if result is not None:
        try:
            result.outgoing_links = _external_links(history.urls(), url)
        except Exception as e:  # never let history parsing break the result
            logger.warning("could not derive outgoing_links on %s: %s", url, e)
        return result
```

(Leave the final `logger.warning("browsing agent returned no structured output ...")` fallback line as-is, after this block.)

- [ ] **Step 3: Run the tests**

Run: `uv run pytest tests/test_browsing.py -v`
Expected: PASS (all browsing tests, including the 2 new `_external_links` tests).

- [ ] **Step 4: Commit**

```bash
git add src/anti_scam_agent/browsing.py tests/test_browsing.py
git commit -m "feat: derive outgoing_links from real browser history trail

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Wire static signals into the pipeline

**Files:**
- Modify: `src/anti_scam_agent/pipeline.py`
- Test: `tests/test_pipeline.py`

- [ ] **Step 1: Update the failing tests**

In `tests/test_pipeline.py`, the `_patch` helper must also stub the (network-doing) collector and accept the new analysis arg. Replace the existing `_patch` function with:

```python
def _patch(monkeypatch, payment_sequence):
    """Stub browsing, analysis, and the static-signal collector; capture args."""
    calls = {"browse": 0, "cards": [], "card_tier": None, "static": None}

    async def fake_browse(url, persona):
        calls["cards"].append(persona.credit_card_number)
        payment = payment_sequence[calls["browse"]]
        calls["browse"] += 1
        return _result(payment)

    async def fake_analyze(result, domain, card_tier, static_signals):
        calls["card_tier"] = card_tier
        calls["static"] = static_signals
        return _assessment()

    def fake_collect(url):
        return StaticSignals(target_host="shop.test")

    monkeypatch.setattr(pipeline, "run_browsing_agent", fake_browse)
    monkeypatch.setattr(pipeline, "run_analysis_agent", fake_analyze)
    monkeypatch.setattr(pipeline, "collect_static_signals", fake_collect)
    return calls
```

Add the import at the top of `tests/test_pipeline.py`:

```python
from anti_scam_agent.signals import StaticSignals
```

Add one new test asserting the static signals reach analysis:

```python
def test_static_signals_passed_to_analysis(monkeypatch):
    calls = _patch(monkeypatch, [Outcome.unclear])
    asyncio.run(pipeline.run_pipeline("http://shop.test"))
    assert isinstance(calls["static"], StaticSignals)
    assert calls["static"].target_host == "shop.test"
```

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: FAIL — `pipeline.collect_static_signals` does not exist; `run_analysis_agent` not yet called with `static_signals`.

- [ ] **Step 2: Implement the wiring**

Replace the contents of `src/anti_scam_agent/pipeline.py` with:

```python
import asyncio
from urllib.parse import urlparse

from anti_scam_agent.analysis import run_analysis_agent
from anti_scam_agent.browsing import run_browsing_agent
from anti_scam_agent.models import Outcome, ScamAssessment
from anti_scam_agent.persona import generate_persona
from anti_scam_agent.signals import collect_static_signals


def _extract_domain(url: str) -> str:
    hostname = urlparse(url).hostname or ""
    return hostname.removeprefix("www.")


async def run_pipeline(url: str) -> ScamAssessment:
    persona = generate_persona()

    # Run 1: a Luhn-invalid card. Acceptance here is the strongest signal.
    result = await run_browsing_agent(url, persona)
    card_tier: str | None = None

    if result.payment_outcome is Outcome.succeeded:
        card_tier = "luhn_invalid"
    elif result.payment_outcome is Outcome.failed:
        # The site's front end caught the bad card. Retry with a valid one;
        # acceptance now (instant success, no processor) is a weaker signal.
        persona_valid = persona.model_copy(
            update={"credit_card_number": persona.credit_card_number_luhn_valid}
        )
        result = await run_browsing_agent(url, persona_valid)
        if result.payment_outcome is Outcome.succeeded:
            card_tier = "luhn_valid"

    # Out-of-band local signals (network I/O off the event loop). Failure-tolerant.
    static_signals = await asyncio.to_thread(collect_static_signals, url)

    domain = _extract_domain(url)
    return await run_analysis_agent(result, domain, card_tier, static_signals)
```

- [ ] **Step 3: Run the tests**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: PASS (all pipeline tests, including the new static-signals test).

- [ ] **Step 4: Commit**

```bash
git add src/anti_scam_agent/pipeline.py tests/test_pipeline.py
git commit -m "feat: collect static signals in pipeline and pass to analysis

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Analysis consumes static signals; remove the tool; new heuristics

**Files:**
- Modify: `src/anti_scam_agent/analysis.py`
- Modify: `CLAUDE.md`
- Test: `tests/test_analysis.py`

- [ ] **Step 1: Add the failing offline guard**

Add to `tests/test_analysis.py`:

```python
def test_run_analysis_agent_accepts_static_signals():
    params = inspect.signature(run_analysis_agent).parameters
    assert "static_signals" in params
```

(`inspect` is already imported from the Bucket 1 work.)

Run: `uv run pytest tests/test_analysis.py::test_run_analysis_agent_accepts_static_signals -v`
Expected: FAIL — `static_signals` not in signature.

- [ ] **Step 2: Rewrite `analysis.py`**

Replace the contents of `src/anti_scam_agent/analysis.py` with:

```python
import logging
from typing import Literal

from agents import Agent, Runner
from dotenv import load_dotenv

from anti_scam_agent.models import BrowsingResult, ScamAssessment
from anti_scam_agent.signals import StaticSignals

load_dotenv()

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a fraud analyst. You are given a structured report from a colleague who visited a target website as an ordinary user, the site's domain, which card tier was used, and a bundle of locally-computed static signals (WHOIS, TLS certificate, DNS). Your job is to judge whether the site is a scam / phishing operation, with reasoning.

All the evidence you need is in the input — there are no tools to call.

The report uses four-state outcomes ('not_attempted', 'failed', 'unclear', 'succeeded'). Only 'succeeded' is a positive signal; 'unclear' is NOT acceptance and must not be treated as one.

Card tier (provided separately):
  - 'luhn_invalid': the site accepted a card number that fails the basic Luhn checksum — a real front end rejects this outright. `payment_outcome='succeeded'` with this tier is the STRONGEST single scam signal.
  - 'luhn_valid': a checksum-valid card was accepted — the stronger Luhn-invalid card had already been rejected by the site's front end before this run. Acceptance here (instant success, no payment-processor redirect) is a SECONDARY (weaker) scam signal.
  - null: no acceptance was observed; do not infer payment fraud.

Static signals (any field may be null when a lookup failed — treat null as 'unknown', never as evidence):
  - domain_info: days_since_creation, days_until_expiration, registrar, registrant_country, privacy_protected.
  - tls: issuer_org, age_days, san_count, is_free_dv (a free domain-validated certificate, e.g. Let's Encrypt/ZeroSSL).
  - dns: has_mx (does the domain accept mail?), nameservers.

Heuristics (combine them — no single signal is definitive):
  - 'luhn_invalid' acceptance = strong evidence; 'luhn_valid' acceptance = moderate evidence.
  - Very young domains (days_since_creation < 90) combined with any payment acceptance or heavy PII collection are strong scam signals.
  - A young domain + a brand-new free DV certificate + no MX record is a classic throwaway-scam fingerprint; together they compound risk, though none alone is conclusive.
  - has_mx=false is a weak negative signal (a real merchant usually has company mail); has_mx=true is mild reassurance. Never decisive alone.
  - privacy_protected and free DV certs are common on legitimate sites too — only let them compound an already-young or payment-positive case.
  - Old, long-expiration domains with normal user flows and an MX record are a weak signal of low risk.
  - Requests for unusually sensitive PII (national ID, bank account, mother's maiden name) alongside other red flags compound risk.
  - Unexpected redirects to unrelated domains (see outgoing_links) after submitting data are suspicious.

ABSTAIN RULE: if `visit_completed` is false, the colleague could not complete the visit, so you have almost no behavioral evidence. In that case do not return a confident scam verdict: cap confidence at 0.4 and lean toward is_scam=false unless the static signals alone are overwhelmingly damning.

Return a ScamAssessment:
  - is_scam: your best binary judgment.
  - confidence: 0.0–1.0, calibrated — not every scam warrants 0.99.
  - scam_type: a short category like 'phishing', 'fake lottery', 'credit card harvesting', or None if not a scam.
  - reasoning: a paragraph citing specific observations from the browsing report and static signals.
  - risk_factors: the concrete items from the inputs that drove your judgment.
"""


async def run_analysis_agent(
    browsing_result: BrowsingResult,
    domain: str,
    card_tier: Literal["luhn_invalid", "luhn_valid"] | None = None,
    static_signals: StaticSignals | None = None,
) -> ScamAssessment:
    agent = Agent(
        name="AnalysisAgent",
        instructions=_SYSTEM_PROMPT,
        output_type=ScamAssessment,
        model="gpt-4.1",
    )

    static_json = static_signals.model_dump_json(indent=2) if static_signals is not None else "null (unavailable)"
    user_message = (
        f"Target domain: {domain}\n"
        f"Card tier: {card_tier if card_tier is not None else 'null (no acceptance observed)'}\n\n"
        f"Static signals (JSON):\n{static_json}\n\n"
        f"Browsing report (JSON):\n{browsing_result.model_dump_json(indent=2)}"
    )

    result = await Runner.run(agent, input=user_message)
    u = result.context_wrapper.usage
    logger.info(f"Requests     : {u.requests}")
    logger.info(f"Input tokens : {u.input_tokens}")
    logger.info(f"Cached tokens: {u.input_tokens_details.cached_tokens}")
    logger.info(f"Output tokens: {u.output_tokens}")
    logger.info(f"Total tokens : {u.total_tokens}")
    return result.final_output_as(ScamAssessment)
```

(The `get_domain_info` tool and its import are removed; the agent now has no tools.)

- [ ] **Step 3: Update the live-test fixtures' call site**

In `tests/test_analysis.py`, the `_run` helper currently forwards `card_tier`. Extend it to also pass static signals so the live tests exercise the new input shape. Replace `_run` with:

```python
def _run(result: BrowsingResult, domain: str, card_tier: str | None = None, static_signals=None) -> ScamAssessment:
    return asyncio.run(run_analysis_agent(result, domain, card_tier, static_signals))
```

(The existing scam/legit live tests keep working — `static_signals` defaults to `None`, and the prompt handles the `null (unavailable)` case.)

Run: `uv run pytest tests/test_analysis.py::test_run_analysis_agent_accepts_static_signals --collect-only -q` then `uv run pytest tests/test_analysis.py::test_run_analysis_agent_accepts_static_signals -v`
Expected: collects cleanly; the offline guard PASSES. Do NOT run the live OpenAI tests.

- [ ] **Step 4: Update CLAUDE.md**

The "Tools convention" bullet in `CLAUDE.md` describes `get_domain_info` as the example tool, and the architecture text says the Analysis Agent "uses the `get_domain_info` tool." Update both to reflect that static signals (WHOIS/TLS/DNS) are now computed in `signals.collect_static_signals` and passed to the Analysis Agent as input, and the Analysis Agent no longer calls tools. Keep the `_name` / underscore-impl testing convention note (still true for `_get_domain_info`, `_get_tls_info`, `_get_dns_info`). Make the edit faithful to the current wording — read the relevant lines first and adjust them rather than rewriting the file.

- [ ] **Step 5: Commit**

```bash
git add src/anti_scam_agent/analysis.py tests/test_analysis.py CLAUDE.md
git commit -m "feat: analysis reads WHOIS/TLS/DNS static signals from input; drop get_domain_info tool

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Full-suite verification + final review

**Files:** none (verification only)

- [ ] **Step 1: Run the offline suite**

Run: `uv run pytest tests/test_models.py tests/test_persona.py tests/test_browsing.py tests/test_pipeline.py tests/test_signals.py -q` and `uv run pytest tests/test_analysis.py -k "card_tier or static_signals" -q` and `uv run pytest tests/test_tools.py -k builder -q`
Expected: all PASS.

- [ ] **Step 2: Confirm full collection (no import errors)**

Run: `uv run pytest --collect-only -q`
Expected: collects cleanly; no reference to the removed `get_domain_info` tool remains. Grep guard: `grep -rn "get_domain_info" src` should show only `_get_domain_info` (underscore) usages — no bare `get_domain_info` symbol.

- [ ] **Step 3: Live tests (optional, network/paid — ask before running)**

The WHOIS test (`tests/test_tools.py::test_get_domain_info`, `tests/test_dependencies.py`), the live OpenAI tests (`tests/test_analysis.py`), and any DNS/TLS network checks require network and (for OpenAI) cost money. Run `uv run pytest` only with the user's go-ahead.

---

## Self-Review notes

- **Spec coverage:** TLS cert (Task 2: issuer/age/SAN/free-DV), DNS MX + nameservers (Task 2), expanded WHOIS registrar/country/privacy (Task 1), `outgoing_links` from history (Task 3), signals delivered as analysis input with the tool removed (Tasks 4–5). ASN explicitly dropped (documented above).
- **Failure tolerance:** `collect_static_signals` wraps every sub-collector in `_safe` (Task 2, tested); `_get_dns_info` swallows per-record failures; `run_browsing_agent` guards the `history.urls()` call (Task 3); the pipeline still always reaches analysis.
- **Blind invariant:** none of the new fields touch `BrowsingResult`'s schema descriptions; `outgoing_links` is an existing neutral field, now populated more accurately. No new agent-visible prompt text mentions fraud framing on the browsing side.
- **Type consistency:** `StaticSignals(target_host, domain_info, tls, dns)`; `TlsInfo(issuer_org, age_days, san_count, is_free_dv)`; `DnsInfo(has_mx, nameservers)`; `DomainInfo` gains `registrar`/`registrant_country`/`privacy_protected`; `run_analysis_agent(browsing_result, domain, card_tier=None, static_signals=None)`; `pipeline.collect_static_signals` patched in tests — all consistent across Tasks 1–5.
