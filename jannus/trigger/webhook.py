"""Layer 1: Trigger — FastAPI webhook, HMAC, LangGraph orchestration."""

from __future__ import annotations

import json
import logging
import threading
import uuid
from typing import Any

import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response

from jannus.agents.graph import get_compiled_graph
from jannus.agents.prompt_builder import build_base_prompt
from jannus.agents.state import JannusState
from jannus.config import get_settings, load_settings
from jannus.trigger.security import verify_github_signature

logger = logging.getLogger("jannus.trigger")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

_graph_lock = threading.Lock()

app = FastAPI(
    title="Jannus",
    version="0.2.0",
    description="GitHub webhook → LangGraph multi-agent → Claude Code",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _run_graph_job(event: str, payload: dict[str, Any], thread_id: str) -> None:
    settings = get_settings()
    initial: JannusState = {
        "event": event,
        "payload": payload,
        "thread_id": thread_id,
        "attempt": 0,
        "max_attempts": settings.max_attempts,
        "review_result": "pending",
    }
    graph = get_compiled_graph()
    cfg: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    try:
        with _graph_lock:
            graph.invoke(initial, config=cfg)
    except Exception:
        logger.exception("Graph invoke failed for thread_id=%s", thread_id)


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
    if build_base_prompt(event, payload, trigger_keywords=kws) is None:
        return Response(
            content=json.dumps({"ok": True, "skipped": True, "reason": "no prompt for this event"}),
            media_type="application/json",
        )

    delivery = request.headers.get("X-GitHub-Delivery", "") or str(uuid.uuid4())
    logger.info(
        "Queue graph job event=%s delivery=%s repo=%s",
        event,
        delivery,
        repo or "?",
    )
    background_tasks.add_task(_run_graph_job, event, payload, delivery)

    return Response(
        content=json.dumps(
            {
                "ok": True,
                "accepted": True,
                "event": event,
                "thread_id": delivery,
            }
        ),
        media_type="application/json",
        status_code=202,
    )


@app.post("/callback")
async def human_callback(request: Request) -> Response:
    """Resume graph after human input (Telegram / external client)."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON") from None

    thread_id = body.get("thread_id")
    message = body.get("message", "")
    if not thread_id:
        raise HTTPException(status_code=400, detail="thread_id required")

    from langgraph.types import Command

    graph = get_compiled_graph()
    cfg: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    try:
        with _graph_lock:
            graph.invoke(Command(resume=message), config=cfg)
    except Exception:
        logger.exception("Callback resume failed thread_id=%s", thread_id)
        raise HTTPException(status_code=500, detail="Resume failed") from None

    return Response(
        content=json.dumps({"ok": True, "resumed": True, "thread_id": thread_id}),
        media_type="application/json",
    )


def run() -> None:
    settings = load_settings()
    uvicorn.run(
        "jannus.trigger.webhook:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )
