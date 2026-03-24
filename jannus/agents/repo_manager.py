"""Clone or update persistent workspaces under WORKSPACES_DIR."""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jannus.agents.state import JannusState
from jannus.config import Settings

logger = logging.getLogger("jannus.repo_manager")


def _sanitize_repo_dir(full_name: str) -> str:
    return full_name.replace("/", "--").lower()


def _load_registry(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"repos": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"repos": {}}


def _save_registry(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _run_git(args: list[str], cwd: str, timeout: int = 300) -> tuple[int, str, str]:
    proc = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def ensure_repo(settings: Settings, state: JannusState) -> dict[str, Any]:
    """Clone if missing, else fetch + pull. Updates registry."""
    full_name = state.get("repo_full_name") or ""
    clone_url = state.get("repo_clone_url") or ""
    if not full_name or not clone_url:
        return {"repo_ready": False, "error": "missing repo_full_name or repo_clone_url"}

    base = settings.workspaces_dir.resolve()
    base.mkdir(parents=True, exist_ok=True)
    local_name = _sanitize_repo_dir(full_name)
    repo_path = base / local_name
    registry_path = settings.registry_path
    reg = _load_registry(registry_path)

    if not repo_path.is_dir() or not (repo_path / ".git").is_dir():
        logger.info("Cloning %s -> %s", clone_url, repo_path)
        code, out, err = _run_git(
            ["git", "clone", clone_url, str(repo_path)],
            cwd=str(base),
            timeout=600,
        )
        if code != 0:
            logger.error("git clone failed: %s %s", out, err)
            return {"repo_ready": False, "error": f"git clone failed: {err or out}"}
        reg.setdefault("repos", {})[full_name] = {
            "path": str(repo_path),
            "clone_url": clone_url,
            "cloned_at": datetime.now(timezone.utc).isoformat(),
        }
    else:
        logger.info("Updating existing clone at %s", repo_path)
        for branch in ("main", "master"):
            c, _, _ = _run_git(["git", "checkout", branch], cwd=str(repo_path), timeout=60)
            if c == 0:
                break
        code, out, err = _run_git(
            ["git", "pull", "--ff-only"],
            cwd=str(repo_path),
            timeout=120,
        )
        if code != 0:
            logger.error("git pull failed: %s %s", out, err)
            return {"repo_ready": False, "error": f"git pull failed: {err or out}"}

    reg.setdefault("repos", {})[full_name] = {
        **reg.get("repos", {}).get(full_name, {}),
        "path": str(repo_path),
        "clone_url": clone_url,
        "last_pull": datetime.now(timezone.utc).isoformat(),
    }
    _save_registry(registry_path, reg)

    if settings.rag_enabled:
        try:
            from jannus.rag.indexer import index_repo

            index_repo(settings, str(repo_path), force=False)
        except Exception as e:
            logger.warning("RAG index after pull failed (non-fatal): %s", e)

    return {"repo_local_path": str(repo_path), "repo_ready": True, "error": ""}
