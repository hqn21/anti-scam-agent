import logging
import os
import time
from datetime import datetime
from email.utils import parseaddr

from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

logger = logging.getLogger(__name__)

_DEFAULT_INBOXES = ["asalpha@agentmail.to", "asbravo@agentmail.to", "ascharlie@agentmail.to"]
_inbox_index = 0


class EmailEvidence(BaseModel):
    polled: bool = False  # did we successfully query the inbox at all
    message_count: int = 0  # messages received in the post-scan window (volume signal)
    from_target_domain: bool = False  # received mail whose sender domain matches the target
    authenticated: bool | None = None  # a domain-matching message passed SPF/DKIM/DMARC; None if none matched


def _get_inboxes() -> list[str]:
    raw = os.getenv("AGENTMAIL_INBOXES", "")
    inboxes = [a.strip() for a in raw.split(",") if a.strip()]
    return inboxes or _DEFAULT_INBOXES


def _reset_inbox_rotation() -> None:
    global _inbox_index
    _inbox_index = 0


def pick_inbox() -> str:
    """Round-robin across the configured inboxes (scans run sequentially)."""
    global _inbox_index
    inboxes = _get_inboxes()
    inbox = inboxes[_inbox_index % len(inboxes)]
    _inbox_index += 1
    return inbox


def _sender_domain(from_field: str) -> str:
    addr = parseaddr(from_field)[1]
    _, _, domain = addr.partition("@")
    return domain.strip().lower()


def _domain_matches(sender_domain: str, target_host: str) -> bool:
    s = sender_domain.lower().removeprefix("www.")
    t = target_host.lower().removeprefix("www.")
    if not s or not t:
        return False
    return s == t or s.endswith("." + t) or t.endswith("." + s)


def _is_authenticated(labels: list[str]) -> bool:
    return "unauthenticated" not in labels


def _evidence_from_messages(messages, target_host: str) -> EmailEvidence:
    matched = [m for m in messages if _domain_matches(_sender_domain(m.from_), target_host)]
    authenticated: bool | None = None
    if matched:
        authenticated = any(_is_authenticated(list(m.labels)) for m in matched)
    return EmailEvidence(
        polled=True,
        message_count=len(messages),
        from_target_domain=bool(matched),
        authenticated=authenticated,
    )


def make_client():
    """Return an AgentMail client, or None when unconfigured (email step is skipped)."""
    api_key = os.getenv("AGENTMAIL_API_KEY")
    if not api_key:
        return None
    try:
        from agentmail import AgentMail

        return AgentMail(api_key=api_key)
    except Exception as e:  # noqa: BLE001
        logger.warning("AgentMail client unavailable: %s", e)
        return None


def collect_email_evidence(
    client,
    inbox: str,
    target_host: str,
    since: datetime,
    poll_seconds: int = 120,
    interval: float = 10.0,
) -> EmailEvidence:
    """Poll the inbox until a domain-matching message arrives or the window closes.

    Never raises: any failure yields EmailEvidence(polled=False).
    """
    deadline = time.monotonic() + poll_seconds
    last: EmailEvidence | None = None
    while True:
        try:
            resp = client.inboxes.messages.list(
                inbox_id=inbox,
                after=since,
                ascending=False,
                limit=50,
                include_unauthenticated=True,
            )
            last = _evidence_from_messages(list(resp.messages), target_host)
            if last.from_target_domain:
                return last  # early exit — got what we came for
        except Exception as e:  # noqa: BLE001 — email signal must never break the pipeline
            logger.warning("email evidence poll failed for %s: %s", inbox, e)
            return EmailEvidence(polled=False)
        if time.monotonic() >= deadline:
            return last or EmailEvidence(polled=True)
        time.sleep(interval)
