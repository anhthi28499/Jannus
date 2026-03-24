"""LangGraph multi-agent orchestration."""

from __future__ import annotations

import logging
import os
from typing import Any

from langgraph.graph import END, START, StateGraph

from jannus.agents.executor import run_claude
from jannus.agents.notifier import notifier_interrupt, notifier_prepare
from jannus.agents.planner import plan
from jannus.agents.prompt_builder import build_prompt_for_graph
from jannus.agents.repo_manager import ensure_repo
from jannus.agents.reviewer import review
from jannus.agents.state import JannusState
from jannus.config import Settings, get_settings

logger = logging.getLogger("jannus.graph")

_compiled: Any = None


def _apply_langsmith_env(settings: Settings) -> None:
    if settings.langchain_api_key:
        os.environ["LANGCHAIN_API_KEY"] = settings.langchain_api_key
    os.environ["LANGCHAIN_PROJECT"] = settings.langchain_project
    os.environ["LANGCHAIN_TRACING_V2"] = "true" if settings.langchain_tracing_v2 else "false"


def _planner_node(state: JannusState) -> dict[str, Any]:
    settings = get_settings()
    _apply_langsmith_env(settings)
    return plan(settings, state)


def _repo_node(state: JannusState) -> dict[str, Any]:
    settings = get_settings()
    return ensure_repo(settings, state)


def _route_after_repo(state: JannusState) -> str:
    if state.get("error") or not state.get("repo_ready"):
        return END
    return "prompt_builder"


def _prompt_node(state: JannusState) -> dict[str, Any]:
    settings = get_settings()
    return build_prompt_for_graph(settings, state)


def _route_after_prompt(state: JannusState) -> str:
    if state.get("skip_graph"):
        return END
    return "executor"


def _executor_node(state: JannusState) -> dict[str, Any]:
    settings = get_settings()
    return run_claude(settings, state)


def _reviewer_node(state: JannusState) -> dict[str, Any]:
    settings = get_settings()
    return review(settings, state)


def _notifier_prepare_node(state: JannusState) -> dict[str, Any]:
    settings = get_settings()
    return notifier_prepare(settings, state)


def _notifier_interrupt_node(state: JannusState) -> dict[str, Any]:
    return notifier_interrupt(state)


def _route_after_review(state: JannusState) -> str:
    if state.get("skip_graph"):
        return END
    r = state.get("review_result")
    if r == "complete":
        return END
    if r == "needs_human":
        return "notifier_prepare"
    max_a = int(state.get("max_attempts") or 3)
    if int(state.get("attempt") or 0) >= max_a:
        return "notifier_prepare"
    if r == "needs_work":
        return "prompt_builder"
    return END


def _build_graph() -> Any:
    import sqlite3

    settings = get_settings()
    _apply_langsmith_env(settings)

    try:
        from langgraph.checkpoint.sqlite import SqliteSaver

        db = settings.checkpoint_db_path.resolve()
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db), check_same_thread=False)
        checkpointer = SqliteSaver(conn)
    except Exception as e:
        logger.warning("SqliteSaver unavailable (%s); using MemorySaver", e)
        from langgraph.checkpoint.memory import MemorySaver

        checkpointer = MemorySaver()

    g = StateGraph(JannusState)
    g.add_node("planner", _planner_node)
    g.add_node("repo_manager", _repo_node)
    g.add_node("prompt_builder", _prompt_node)
    g.add_node("executor", _executor_node)
    g.add_node("reviewer", _reviewer_node)
    g.add_node("notifier_prepare", _notifier_prepare_node)
    g.add_node("notifier_interrupt", _notifier_interrupt_node)

    g.add_edge(START, "planner")
    g.add_edge("planner", "repo_manager")
    g.add_conditional_edges("repo_manager", _route_after_repo)
    g.add_conditional_edges("prompt_builder", _route_after_prompt)
    g.add_edge("executor", "reviewer")
    g.add_conditional_edges("reviewer", _route_after_review)
    g.add_edge("notifier_prepare", "notifier_interrupt")
    g.add_edge("notifier_interrupt", "prompt_builder")

    return g.compile(checkpointer=checkpointer)


def get_compiled_graph() -> Any:
    global _compiled
    if _compiled is None:
        _compiled = _build_graph()
    return _compiled


