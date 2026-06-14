import logging
import os
import threading
from typing import TYPE_CHECKING

from dotenv import load_dotenv

if TYPE_CHECKING:
    from agentmail import AgentMail

load_dotenv()

logger = logging.getLogger(__name__)

_DEFAULT_INBOXES = ["asalpha@agentmail.to", "asbravo@agentmail.to", "ascharlie@agentmail.to"]
_inbox_index = 0
_inbox_lock = threading.Lock()


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
    with _inbox_lock:
        inbox = inboxes[_inbox_index % len(inboxes)]
        _inbox_index += 1
    return inbox


def make_client() -> "AgentMail":
    """Return an AgentMail client. AgentMail is mandatory: raise if unconfigured."""
    api_key = os.getenv("AGENTMAIL_API_KEY")
    if not api_key:
        raise RuntimeError(
            "AGENTMAIL_API_KEY is required: the agent routes the persona's email "
            "through an AgentMail inbox so it can read verification codes mid-flow. "
            "Set it in .env (see .env.example)."
        )
    from agentmail import AgentMail

    return AgentMail(api_key=api_key)


def _message_text(client, inbox: str, m) -> str:
    """Best-effort body text for a message, tolerant of the SDK's exact shape."""
    for attr in ("text", "preview"):
        val = getattr(m, attr, None)
        if val:
            return str(val)
    msg_id = getattr(m, "message_id", None) or getattr(m, "id", None)
    if msg_id is None:
        return ""
    try:
        full = client.inboxes.messages.get(inbox_id=inbox, message_id=msg_id)
        return str(getattr(full, "text", "") or getattr(full, "preview", "") or "")
    except Exception as e:  # noqa: BLE001 — reading mail must never break browsing
        logger.warning("read_inbox_text get failed for %s: %s", inbox, e)
        return ""


def read_inbox_text(client, inbox: str, limit: int = 5) -> str:
    """Readable text of the most recent inbox messages, for the browsing email tool.

    Failure-tolerant: any error yields a benign string (never raises). Includes
    unauthenticated mail — scam verification mail is frequently unauthenticated.
    """
    try:
        resp = client.inboxes.messages.list(
            inbox_id=inbox,
            ascending=False,
            limit=limit,
            include_unauthenticated=True,
        )
        messages = list(resp.messages)
    except Exception as e:  # noqa: BLE001
        logger.warning("read_inbox_text list failed for %s: %s", inbox, e)
        return "Could not read the inbox right now; please continue."
    if not messages:
        return "No messages in your inbox yet."
    parts = []
    for m in messages:
        subject = getattr(m, "subject", "") or ""
        body = _message_text(client, inbox, m)
        parts.append(f"From: {getattr(m, 'from_', '')}\nSubject: {subject}\n{body}".strip())
    return "\n\n---\n\n".join(parts)
