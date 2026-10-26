"""Smoke-test retrieval against the local ChromaDB index.

Usage:
    uv run python scripts/verify_index.py "How many days of annual leave?"
    uv run python scripts/verify_index.py "VPN setup" --document-id <uuid> --top-k 3
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.services.bm25_index import get_bm25_index  # noqa: E402
from app.services.embeddings import Embedder  # noqa: E402
from app.services.errors import (  # noqa: E402
    EmbeddingNotConfiguredError,
    EmbeddingServiceError,
    VectorStoreError,
)
from app.services.retrieval import retrieve_documents  # noqa: E402
from app.services.vector_store import VectorStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", help="Natural-language question to search for")
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--document-id", default=None, help="Restrict hits to one document")
    parser.add_argument(
        "--no-hybrid", action="store_true", help="Vector-only (skip BM25 fusion)"
    )
    args = parser.parse_args()

    settings = get_settings()
    top_k = args.top_k or settings.retrieval_top_k
    try:
        embedder = Embedder(
            api_key=settings.openai_api_key,
            model=settings.embedding_model,
            batch_size=settings.embedding_batch_size,
            max_retries=settings.embedding_max_retries,
        )
        store = VectorStore(settings.chroma_path, settings.vector_collection)
        outcome = retrieve_documents(
            args.question,
            embedder=embedder,
            store=store,
            top_k=top_k,
            document_id=args.document_id,
            bm25=get_bm25_index(),
            hybrid=settings.retrieval_hybrid and not args.no_hybrid,
            rrf_k=settings.retrieval_rrf_k,
        )
    except (EmbeddingNotConfiguredError, EmbeddingServiceError, VectorStoreError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    hits = outcome.results
    if not hits:
        print("No matching chunks found (is the index populated?).")
        return 0

    for rank, hit in enumerate(hits, start=1):
        print(
            f"{rank}. score={hit.score:.4f}  "
            f"file={hit.filename}  page={hit.page}  "
            f"chunk={hit.chunk_index}"
        )
        snippet = " ".join(hit.text.split())
        print(f"   {snippet[:240]}{'...' if len(snippet) > 240 else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
