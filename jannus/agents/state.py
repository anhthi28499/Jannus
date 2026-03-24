"""Shared LangGraph state for Jannus multi-agent runs."""

from __future__ import annotations

from typing import Any, Literal, TypedDict


class JannusState(TypedDict, total=False):
    """State passed between graph nodes; nodes return partial updates."""

    event: str
    payload: dict[str, Any]
    thread_id: str

    repo_full_name: str
    repo_clone_url: str
    repo_local_path: str
    repo_ready: bool
    planner_summary: str
    task_type: str

    prompt: str
    claude_output: str
    claude_stderr: str
    claude_exit_code: int

    review_result: Literal["complete", "needs_work", "needs_human", "pending"]
    review_feedback: str

    human_response: str | None
    attempt: int
    max_attempts: int

    error: str
    skip_graph: bool
