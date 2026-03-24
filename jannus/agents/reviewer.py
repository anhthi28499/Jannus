"""LLM reviewer: evaluate Claude output and decide next step."""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Any

from jannus.agents.state import JannusState
from jannus.config import Settings

logger = logging.getLogger("jannus.reviewer")


def _git_diff(repo: str, max_chars: int = 8000) -> str:
    try:
        proc = subprocess.run(
            ["git", "diff", "--stat", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=30,
        )
        s = (proc.stdout or "") + (proc.stderr or "")
        return s[:max_chars]
    except (subprocess.SubprocessError, OSError) as e:
        return f"(diff unavailable: {e})"


def _heuristic_review(state: JannusState) -> dict[str, Any]:
    code = state.get("claude_exit_code", 1)
    out = (state.get("claude_output") or "")[:2000]
    if code == 0 and "error" not in out.lower() and "fail" not in out.lower():
        return "complete", "Heuristic: exit 0 and no obvious error keywords in output."
    if code != 0:
        return "needs_work", f"Claude exited with code {code}. Improve the fix and re-run tests."
    return "needs_work", "Heuristic: please verify the changes and ensure tests pass."


def _llm_review(settings: Settings, state: JannusState) -> dict[str, Any]:
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI

    repo = state.get("repo_local_path") or ""
    diff = _git_diff(repo) if repo else ""
    model = ChatOpenAI(
        model=settings.openai_model,
        api_key=settings.openai_api_key or None,
        temperature=0.1,
    )
    sys = SystemMessage(
        content=(
            "You are a senior code reviewer. Given Claude's terminal output and optional git diff, "
            "decide if the task is done. Reply with JSON only: "
            '{"result":"complete"|"needs_work"|"needs_human","feedback":"..."} '
            "Use needs_human if you need clarification from a human. "
            "Use needs_work if more code changes are likely needed. "
            "Use complete if the output indicates success (PR created, tests pass, etc.)."
        )
    )
    human = HumanMessage(
        content=(
            f"exit_code={state.get('claude_exit_code')}\n\n"
            f"stdout:\n{state.get('claude_output', '')[:12000]}\n\n"
            f"stderr:\n{state.get('claude_stderr', '')[:4000]}\n\n"
            f"git diff stat:\n{diff}\n"
        )
    )
    resp = model.invoke([sys, human])
    text = getattr(resp, "content", str(resp))
    try:
        data = json.loads(text[text.find("{") : text.rfind("}") + 1])
    except (json.JSONDecodeError, ValueError):
        r, fb = _heuristic_review(state)
        return {"review_result": r, "review_feedback": fb}
    r = data.get("result", "needs_work")
    if r not in ("complete", "needs_work", "needs_human"):
        r = "needs_work"
    return {"review_result": r, "review_feedback": data.get("feedback") or ""}


def review(settings: Settings, state: JannusState) -> dict[str, Any]:
    if state.get("skip_graph"):
        return {}
    if settings.openai_api_key:
        try:
            return _llm_review(settings, state)
        except Exception as e:
            logger.exception("Reviewer LLM failed: %s", e)
            r, fb = _heuristic_review(state)
            return {"review_result": r, "review_feedback": fb}
    r, fb = _heuristic_review(state)
    return {"review_result": r, "review_feedback": fb}
