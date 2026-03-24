"""GitHub webhook → `git pull` → `claude -p` (Claude Code CLI).

Setup: copy `.env.example` to `.env`, set `REPO_PATH` to your local clone.

Run from project root::

    python -m claude_webhook_service

Point GitHub's webhook to ``POST /webhook`` (use ngrok or a public URL). Set
``WEBHOOK_SECRET`` to match GitHub; use ``WEBHOOK_DRY_RUN=true`` to test
without running git or Claude.
"""
from __future__ import annotations

import json
import logging
import subprocess
import threading
from typing import Any

import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response

from claude_webhook_service.config import Settings, load_settings
from claude_webhook_service.github_verify import verify_github_signature
from claude_webhook_service.prompts import build_prompt

logger = logging.getLogger("claude_webhook")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

_settings: Settings | None = None
_job_lock = threading.Lock()


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


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


def process_webhook_job(event: str, prompt: str) -> None:
    settings = get_settings()

    if settings.webhook_dry_run:
        logger.info("WEBHOOK_DRY_RUN: would run claude with prompt (%s chars):\n%s", len(prompt), prompt[:4000])
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


def webhook_worker(event: str, prompt: str) -> None:
    if not _job_lock.acquire(blocking=False):
        logger.warning("Another job is running; skipping this delivery.")
        return
    try:
        process_webhook_job(event, prompt)
    finally:
        _job_lock.release()


app = FastAPI(title="Claude Code Webhook", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/webhook")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    settings = get_settings()
    raw = await request.body()
    sig = request.headers.get("X-Hub-Signature-256")
    if not verify_github_signature(raw, settings.webhook_secret, sig):
        raise HTTPException(status_code=401, detail="Invalid signature")

    event = (request.headers.get("X-GitHub-Event") or "").strip().lower()
    if not event:
        raise HTTPException(status_code=400, detail="Missing X-GitHub-Event")

    if event == "ping":
        return Response(content=json.dumps({"ok": True, "ping": True}), media_type="application/json")

    allow_events = settings.parsed_event_allowlist()
    if allow_events and event not in allow_events:
        return Response(
            content=json.dumps({"ok": True, "skipped": True, "reason": "event not in allowlist"}),
            media_type="application/json",
        )

    try:
        payload: dict[str, Any] = json.loads(raw.decode("utf-8") or "{}")
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}") from e

    repo = (payload.get("repository") or {}).get("full_name") or ""
    allow_repos = settings.parsed_repo_allowlist()
    if allow_repos and repo.lower() not in allow_repos:
        return Response(
            content=json.dumps({"ok": True, "skipped": True, "reason": "repo not in allowlist"}),
            media_type="application/json",
        )

    prompt = build_prompt(event, payload)
    if prompt is None:
        return Response(
            content=json.dumps({"ok": True, "skipped": True, "reason": "no prompt for this event"}),
            media_type="application/json",
        )

    delivery = request.headers.get("X-GitHub-Delivery", "")
    logger.info(
        "Queue job event=%s delivery=%s repo=%s",
        event,
        delivery,
        repo or "?",
    )
    background_tasks.add_task(webhook_worker, event, prompt)

    return Response(
        content=json.dumps(
            {
                "ok": True,
                "accepted": True,
                "event": event,
                "delivery": delivery,
            }
        ),
        media_type="application/json",
        status_code=202,
    )


def run() -> None:
    settings = load_settings()
    uvicorn.run(
        "claude_webhook_service.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    run()
