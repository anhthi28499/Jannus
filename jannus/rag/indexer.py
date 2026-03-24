"""Optional full re-index of a workspace (calls retriever ensure_index)."""

from __future__ import annotations

from jannus.config import Settings


def index_repo(settings: Settings, repo_path: str, *, force: bool = True) -> None:
    """Rebuild vector store for ``repo_path`` when RAG is enabled."""
    from jannus.rag.retriever import ensure_index

    ensure_index(settings, repo_path, force=force)
