"""Planner: classify GitHub event and fill repo fields (optional OpenAI)."""

from __future__ import annotations

import json
import logging
from typing import Any

from jannus.agents.state import JannusState
from jannus.config import Settings

logger = logging.getLogger("jannus.planner")


def _heuristic_plan(state: JannusState) -> dict[str, Any]:
    payload = state.get("payload") or {}
    repo = payload.get("repository") or {}
    full_name = repo.get("full_name") or "unknown/unknown"
    clone_url = repo.get("clone_url") or ""
    event = state.get("event") or "unknown"
    return {
        "repo_full_name": full_name,
        "repo_clone_url": clone_url,
        "task_type": event,
        "planner_summary": f"Process GitHub event `{event}` for repository `{full_name}`.",
    }


def _llm_plan(settings: Settings, state: JannusState) -> dict[str, Any]:
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI

    payload = state.get("payload") or {}
    model = ChatOpenAI(
        model=settings.openai_model,
        api_key=settings.openai_api_key or None,
        temperature=0.2,
    )
    sys = SystemMessage(
        content=(
            "You are a planner for a coding agent. Given a GitHub webhook event name and JSON payload, "
            "output a short JSON object with keys: task_type (string), summary (string), "
            "repo_full_name (owner/repo), repo_clone_url (https clone URL from payload.repository.clone_url)."
        )
    )
    human = HumanMessage(
        content=f"event={state.get('event')}\n\npayload=\n{json.dumps(payload, default=str)[:12000]}"
    )
    resp = model.invoke([sys, human])
    text = getattr(resp, "content", str(resp))
    try:
        data = json.loads(text[text.find("{") : text.rfind("}") + 1])
    except (json.JSONDecodeError, ValueError):
        logger.warning("Planner LLM returned non-JSON; using heuristic")
        return _heuristic_plan(state)
    return {
        "repo_full_name": data.get("repo_full_name") or _heuristic_plan(state)["repo_full_name"],
        "repo_clone_url": data.get("repo_clone_url") or _heuristic_plan(state)["repo_clone_url"],
        "task_type": data.get("task_type") or state.get("event", "unknown"),
        "planner_summary": data.get("summary") or _heuristic_plan(state)["planner_summary"],
    }


def plan(settings: Settings, state: JannusState) -> dict[str, Any]:
    if state.get("skip_graph"):
        return {}
    if settings.openai_api_key:
        try:
            return _llm_plan(settings, state)
        except Exception as e:
            logger.exception("Planner LLM failed: %s", e)
            return _heuristic_plan(state)
    return _heuristic_plan(state)
