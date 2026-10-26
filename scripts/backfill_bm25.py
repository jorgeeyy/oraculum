"""Rebuild the BM25 (SQLite FTS5) term index from the existing vector store.

Run once after deploying hybrid retrieval, and again if data/bm25.db is ever
lost or corrupted. Reads stored chunk texts from Chroma and rewrites the FTS
index from scratch - documents, vectors, and Postgres records are untouched.

Idempotent: the index is cleared, then every chunk is re-indexed in batches.

    uv run python scripts/backfill_bm25.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.schemas.documents import Chunk  # noqa: E402
from app.services.bm25_index import get_bm25_index  # noqa: E402
from app.services.vector_store import get_vector_store  # noqa: E402

BATCH_SIZE = 200


def main() -> int:
    settings = get_settings()
    store = get_vector_store()
    bm25 = get_bm25_index()

    stored = store.all_chunks()
    print(f"Found {len(stored)} chunks in the vector store ({settings.chroma_path})")

    bm25.clear()
    indexed = 0
    for start in range(0, len(stored), BATCH_SIZE):
        batch = stored[start : start + BATCH_SIZE]
        chunks = [
            Chunk(
                document_id=str(item["metadata"].get("document_id", "")),
                filename=str(item["metadata"].get("filename", "")),
                page=int(item["metadata"].get("page", 0)),
                chunk_index=int(item["metadata"].get("chunk_index", -1)),
                token_count=int(item["metadata"].get("token_count", 0)),
                text=item["text"],
            )
            for item in batch
        ]
        bm25.index_chunks(chunks)
        indexed += len(chunks)

    print(f"Indexed {indexed} chunks into {settings.bm25_path}")
    print(f"Index now contains {bm25.count()} rows (expected {len(stored)})")
    return 0 if bm25.count() == len(stored) else 1


if __name__ == "__main__":
    raise SystemExit(main())
