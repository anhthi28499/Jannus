"""Run Claude Code CLI in the workspace clone."""

from __future__ import annotations

import logging
import subprocess
import time
from typing import Any

from jannus.agents.state import JannusState
from jannus.config import Settings

logger = logging.getLogger("jannus.executor")


def run_claude(settings: Settings, state: JannusState) -> dict[str, Any]:
    """Execute ``claude -p``; poll subprocess so the graph does not block the event loop unnecessarily."""
    if state.get("skip_graph"):
        return {}
    if settings.webhook_dry_run:
        return {
            "claude_output": "[dry-run] skipped claude",
            "claude_stderr": "",
            "claude_exit_code": 0,
            "attempt": state.get("attempt", 0) + 1,
        }

    repo = state.get("repo_local_path") or ""
    prompt = state.get("prompt") or ""
    if not repo or not prompt:
        return {"error": "missing repo_local_path or prompt", "claude_exit_code": 1}

    cmd = [settings.claude_bin, "-p", prompt, *settings.claude_extra_argv()]
    logger.info("Running claude (prompt len=%s) in %s", len(prompt), repo)

    proc = subprocess.Popen(
        cmd,
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + settings.claude_timeout
    while proc.poll() is None:
        if time.monotonic() > deadline:
            proc.kill()
            out, err = proc.communicate(timeout=5)
            return {
                "claude_output": out or "",
                "claude_stderr": (err or "") + "\n[timeout]",
                "claude_exit_code": -1,
                "attempt": state.get("attempt", 0) + 1,
            }
        time.sleep(0.5)

    out, err = proc.communicate()
    code = proc.returncode if proc.returncode is not None else -1
    return {
        "claude_output": out or "",
        "claude_stderr": err or "",
        "claude_exit_code": code,
        "attempt": state.get("attempt", 0) + 1,
    }
