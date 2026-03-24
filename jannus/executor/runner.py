"""Layer 3: Executor — git pull + Claude Code CLI."""

from __future__ import annotations

import logging
import subprocess
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jannus.config import Settings

logger = logging.getLogger("jannus.executor")

_job_lock = threading.Lock()


def _run_git_pull(repo: str) -> tuple[int, str, str]:
    proc = subprocess.run(
        ["git", "pull", "--ff-only"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _run_claude(settings: Settings, prompt: str) -> tuple[int, str, str]:
    cmd = [settings.claude_bin, "-p", prompt, *settings.claude_extra_argv()]
    logger.info("Running: %s ... (prompt length=%s)", settings.claude_bin, len(prompt))
    proc = subprocess.run(
        cmd,
        cwd=str(settings.repo_path),
        capture_output=True,
        text=True,
        timeout=settings.claude_timeout,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def process_webhook_job(settings: Settings, prompt: str) -> None:
    if settings.webhook_dry_run:
        logger.info(
            "WEBHOOK_DRY_RUN: would run claude with prompt (%s chars):\n%s",
            len(prompt),
            prompt[:4000],
        )
        return

    repo = str(settings.repo_path)
    if not settings.repo_path.is_dir():
        logger.error("REPO_PATH is not a directory: %s", repo)
        return

    code, out, err = _run_git_pull(repo)
    if code != 0:
        logger.error("git pull failed (%s): %s %s", code, out, err)
        return

    c2, o2, e2 = _run_claude(settings, prompt)
    if c2 != 0:
        logger.error("claude exited %s stderr=%s stdout_tail=%s", c2, e2, o2[-2000:])
    else:
        logger.info("claude finished OK. stdout_tail=%s", o2[-2000:])


def webhook_worker(settings: Settings, prompt: str) -> None:
    if not _job_lock.acquire(blocking=False):
        logger.warning("Another job is running; skipping this delivery.")
        return
    try:
        process_webhook_job(settings, prompt)
    finally:
        _job_lock.release()
