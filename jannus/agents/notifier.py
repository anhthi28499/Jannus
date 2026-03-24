"""Telegram notifications and human-in-the-loop via LangGraph interrupt."""

from __future__ import annotations

import logging
from typing import Any

from jannus.agents.state import JannusState
from jannus.config import Settings

logger = logging.getLogger("jannus.notifier")


def _send_telegram(settings: Settings, text: str) -> None:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        logger.warning("Telegram not configured; skipping send")
        return
    import httpx

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": settings.telegram_chat_id,
        "text": text[:4000],
        "parse_mode": "HTML",
    }
    try:
        r = httpx.post(url, json=payload, timeout=30.0)
        r.raise_for_status()
    except Exception as e:
        logger.exception("Telegram send failed: %s", e)


def notifier_prepare(settings: Settings, state: JannusState) -> dict[str, Any]:
    """Send Telegram (runs when routing from reviewer, not on interrupt resume)."""
    tid = state.get("thread_id") or "unknown"
    summary = state.get("planner_summary") or ""
    out = (state.get("claude_output") or "")[:1500]
    fb = state.get("review_feedback") or ""
    body = (
        f"<b>Jannus cần input</b>\n"
        f"thread_id: <code>{tid}</code>\n"
        f"{summary}\n\n"
        f"<b>Claude output (truncated)</b>\n<pre>{out}</pre>\n"
        f"<b>Reviewer</b>\n{fb}\n\n"
        f"Reply via POST /callback with JSON {{\"thread_id\":\"...\",\"message\":\"...\"}}"
    )
    _send_telegram(settings, body)
    return {}


def notifier_interrupt(state: JannusState) -> dict[str, Any]:
    """Block on interrupt until resumed via ``Command(resume=...)``."""
    from langgraph.types import interrupt

    tid = state.get("thread_id") or "unknown"
    resumed = interrupt({"thread_id": tid, "message": "Awaiting human response"})
    return {"human_response": str(resumed) if resumed is not None else ""}
