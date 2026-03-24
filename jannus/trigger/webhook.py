"""Layer 1: Trigger — FastAPI webhook endpoint and request validation."""

from __future__ import annotations

import json
import logging
from typing import Any

import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response

from jannus.config import get_settings, load_settings
from jannus.executor.runner import webhook_worker
from jannus.prompt.builder import build_prompt
from jannus.trigger.security import verify_github_signature

logger = logging.getLogger("jannus.trigger")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

app = FastAPI(title="Jannus", version="0.1.0", description="GitHub webhook → Claude Code")


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

    kws = settings.parsed_trigger_keywords()
    prompt = build_prompt(event, payload, trigger_keywords=kws)
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
    background_tasks.add_task(webhook_worker, settings, prompt)

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
        "jannus.trigger.webhook:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )
