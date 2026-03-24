"""Retrieve short context from indexed repo (LlamaIndex + Chroma) or empty string."""

from __future__ import annotations

import logging
from pathlib import Path

from jannus.config import Settings

logger = logging.getLogger("jannus.rag.retriever")

_INDEX_CACHE: dict[str, object] = {}


def ensure_index(settings: Settings, repo_path: str, *, force: bool = False) -> None:
    """Load or build persisted Chroma index for ``repo_path``."""
    if not settings.rag_enabled or not settings.openai_api_key:
        return
    root = Path(repo_path).resolve()
    if not root.is_dir():
        return
    key = str(root)
    if not force and key in _INDEX_CACHE:
        return
    try:
        import chromadb
        from llama_index.core import SimpleDirectoryReader, StorageContext, VectorStoreIndex
        from llama_index.embeddings.openai import OpenAIEmbedding
        from llama_index.vector_stores.chroma import ChromaVectorStore
    except ImportError as e:
        logger.warning("RAG optional deps missing: %s", e)
        return

    slug = root.name.replace("/", "-")
    persist = settings.workspaces_dir / ".chroma" / slug
    persist.mkdir(parents=True, exist_ok=True)

    reader = SimpleDirectoryReader(
        input_dir=str(root),
        recursive=True,
        exclude=[
            "**/.git/**",
            "**/node_modules/**",
            "**/.venv/**",
            "**/__pycache__/**",
            "**/workspaces/**",
        ],
    )
    docs = reader.load_data()
    if not docs:
        return

    embed = OpenAIEmbedding(api_key=settings.openai_api_key)
    client = chromadb.PersistentClient(path=str(persist))
    collection = client.get_or_create_collection(f"jannus_{slug}")
    vs = ChromaVectorStore(chroma_collection=collection)
    sc = StorageContext.from_defaults(vector_store=vs)
    index = VectorStoreIndex.from_documents(docs, storage_context=sc, embed_model=embed)
    _INDEX_CACHE[key] = index


def retrieve_context(settings: Settings, repo_path: str, query: str) -> str:
    """Return a short text context for ``query`` or empty string."""
    if not settings.rag_enabled or not query.strip():
        return ""
    ensure_index(settings, repo_path, force=False)
    key = str(Path(repo_path).resolve())
    index = _INDEX_CACHE.get(key)
    if index is None:
        return ""
    try:
        qe = index.as_query_engine(similarity_top_k=4)
        resp = qe.query(query[:2000])
        return str(getattr(resp, "response", resp))[:4000]
    except Exception as e:
        logger.warning("RAG query failed: %s", e)
        return ""
