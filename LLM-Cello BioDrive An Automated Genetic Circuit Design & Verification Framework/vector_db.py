"""
Vector DB builder + search for Cello parts.

This module:
- Creates a persistent local ChromaDB in ./chroma_db
- Builds a collection named `cello_parts`
- Uses LiteLLM embeddings (supports OpenAI/Anthropic/Google/Ollama/etc.)
- Provides:
    - build_database(parts_list, embedding_model, api_key=None, api_base=None)
    - search_parts(query_text, embedding_model, api_key=None, api_base=None, n_results=5)
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List, Sequence

import chromadb
import litellm
import litellm.exceptions
from chromadb.api.types import QueryResult


CHROMA_DIR = Path(__file__).resolve().parent / "chroma_db"
COLLECTION_NAME = "cello_parts"
DEFAULT_EMBED_MODEL_NAME = "ollama/nomic-embed-text"


def _sha256_id(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    # Chroma id: allow [a-zA-Z0-9._-] and length constraints are internal; keep it simple.
    return f"part_{digest[:16]}"


def _batch_iter(items: Sequence[str], batch_size: int) -> Iterable[Sequence[str]]:
    for i in range(0, len(items), batch_size):
        yield items[i : i + batch_size]


def _embed_texts(
    texts: Sequence[str],
    *,
    embedding_model: str,
    api_key: str | None = None,
    api_base: str | None = None,
    batch_size: int = 64,
) -> List[List[float]]:
    """
    Embed texts using LiteLLM.

    Returns a list of embeddings (each embedding is a list[float]).
    """
    all_embeddings: List[List[float]] = []
    for chunk in _batch_iter(list(texts), batch_size=batch_size):
        resp = litellm.embedding(
            model=embedding_model,
            input=list(chunk),
            api_key=api_key.strip() if api_key and api_key.strip() else None,
            api_base=api_base.strip() if api_base and api_base.strip() else None,
        )
        # LiteLLM EmbeddingResponse: resp.data[i].embedding
        all_embeddings.extend([d["embedding"] for d in resp.data])
    return all_embeddings


@dataclass(frozen=True)
class VectorDB:
    client: chromadb.PersistentClient
    collection: chromadb.api.models.Collection.Collection


def init_vector_db(
    *,
    chroma_dir: Path = CHROMA_DIR,
    collection_name: str = COLLECTION_NAME,
) -> VectorDB:
    """
    Initialize persistent ChromaDB and ensure the collection exists.
    """
    chroma_dir.mkdir(parents=True, exist_ok=True)

    client = chromadb.PersistentClient(path=str(chroma_dir))

    collection = client.get_or_create_collection(
        name=collection_name,
    )
    return VectorDB(client=client, collection=collection)


def build_database(
    parts_list: List[str],
    embedding_model: str,
    api_key: str | None = None,
    *,
    api_base: str | None = None,
    batch_size: int = 64,
) -> None:
    """
    Build (or extend) the vector database from a list of part descriptions.

    Dedup strategy:
    - Each description gets a deterministic ID derived from sha256(text).
    - We only add missing IDs to avoid duplicate writes.
    """
    if not embedding_model or not str(embedding_model).strip():
        raise ValueError("embedding_model is required.")
    if not parts_list:
        return

    vdb = init_vector_db()
    collection = vdb.collection

    part_texts: List[str] = [p for p in parts_list if isinstance(p, str) and p.strip()]
    if not part_texts:
        return

    ids = [_sha256_id(t) for t in part_texts]

    # Find which IDs already exist.
    # Note: Collection.get can return metadatas/documents by default; that's ok for correctness.
    try:
        existing = collection.get(ids=ids)
        existing_ids = set(existing.get("ids", []))
    except Exception:
        # If collection.get fails for some reason, fall back to "no ids exist"
        # rather than failing the whole ingestion.
        existing_ids = set()

    missing_texts: List[str] = []
    missing_ids: List[str] = []
    for t, pid in zip(part_texts, ids):
        if pid not in existing_ids:
            missing_texts.append(t)
            missing_ids.append(pid)

    if not missing_texts:
        return

    # Embed only missing documents.
    try:
        missing_embeddings = _embed_texts(
            missing_texts,
            embedding_model=embedding_model,
            api_key=api_key,
            api_base=api_base,
            batch_size=batch_size,
        )
    except litellm.exceptions.AuthenticationError as e:
        raise ValueError("Embedding 供應商 API 驗證失敗，請確認 API Key 是否正確與有效。") from e
    except litellm.exceptions.RateLimitError as e:
        raise RuntimeError("Embedding 供應商使用上限或額度不足，請稍後再試。") from e
    except litellm.exceptions.BadRequestError as e:
        raise RuntimeError(f"Embedding 請求參數不正確：{str(e)}") from e
    except litellm.exceptions.APIError as e:
        raise RuntimeError(f"呼叫 Embedding 供應商 API 發生問題：{str(e)}") from e
    except Exception as e:
        raise RuntimeError(f"Embedding 過程發生未預期錯誤：{str(e)}") from e

    # Write to Chroma.
    try:
        collection.add(
            ids=missing_ids,
            documents=missing_texts,
            embeddings=missing_embeddings,
        )
    except Exception as e:
        raise RuntimeError(f"Chroma 寫入失敗：{str(e)}") from e


def search_parts(
    query_text: str,
    embedding_model: str,
    api_key: str | None = None,
    *,
    api_base: str | None = None,
    n_results: int = 5,
) -> List[str]:
    """
    Search similar parts by natural-language query.

    Returns:
    - top-k similar `documents` (the stored natural-language descriptions).
    """
    if not embedding_model or not str(embedding_model).strip():
        raise ValueError("embedding_model is required.")
    if not query_text or not query_text.strip():
        return []
    if n_results <= 0:
        return []

    vdb = init_vector_db()
    collection = vdb.collection

    try:
        query_embeddings = _embed_texts(
            [query_text],
            embedding_model=embedding_model,
            api_key=api_key,
            api_base=api_base,
            batch_size=1,
        )
    except litellm.exceptions.AuthenticationError as e:
        raise ValueError("Embedding 供應商 API 驗證失敗，無法進行檢索。") from e
    except litellm.exceptions.RateLimitError as e:
        raise RuntimeError("Embedding 供應商使用上限或額度不足，請稍後再試。") from e
    except litellm.exceptions.BadRequestError as e:
        raise RuntimeError(f"Embedding 請求參數不正確：{str(e)}") from e
    except litellm.exceptions.APIError as e:
        raise RuntimeError(f"呼叫 Embedding 供應商 API 發生問題：{str(e)}") from e
    except Exception as e:
        raise RuntimeError(f"Query embedding 過程發生未預期錯誤：{str(e)}") from e

    try:
        result: QueryResult = collection.query(
            query_embeddings=query_embeddings,
            n_results=n_results,
            include=["documents", "distances"],
        )
    except Exception as e:
        raise RuntimeError(f"Chroma 檢索失敗：{str(e)}") from e

    # Chroma returns lists per query. Here we passed only one query.
    docs = result.get("documents", [[]])
    if not docs:
        return []
    return [d for d in docs[0] if isinstance(d, str)]


def main() -> None:
    """
    Optional CLI for quick smoke tests.
    Example:
      py vector_db.py --query "我需要一個能感測 IPTG 的輸入元件"
    """
    import argparse
    import ast

    parser = argparse.ArgumentParser(description="Build/search cello_parts vector DB.")
    parser.add_argument("--parts", default=None, help="A Python list literal of parts descriptions.")
    parser.add_argument("--ucf_parts_file", default=None, help="Path to a .json or .txt holding a Python list literal.")
    parser.add_argument("--embedding_model", default=DEFAULT_EMBED_MODEL_NAME, help="Embedding model name (e.g., ollama/nomic-embed-text).")
    parser.add_argument("--api_key", default=None, help="Embedding provider API Key (optional for local).")
    parser.add_argument("--api_base", default=None, help="Embedding provider API base (e.g., http://localhost:11434 for Ollama).")
    parser.add_argument("--query", default=None, help="Natural language query to search.")
    parser.add_argument("--n_results", type=int, default=5, help="Top-k results to return.")
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("EMBEDDING_API_KEY", "") or None

    parts: List[str] = []
    if args.parts:
        parts = list(ast.literal_eval(args.parts))
    elif args.ucf_parts_file:
        p = Path(args.ucf_parts_file)
        raw = p.read_text(encoding="utf-8")
        parts = list(ast.literal_eval(raw))

    if parts:
        build_database(
            parts,
            embedding_model=args.embedding_model,
            api_key=api_key,
            api_base=args.api_base,
        )
        print("Ingestion completed.")

    if args.query:
        top = search_parts(
            args.query,
            embedding_model=args.embedding_model,
            api_key=api_key,
            api_base=args.api_base,
            n_results=args.n_results,
        )
        print(top)


if __name__ == "__main__":
    main()

