"""BM25 term matching over chunk text, backed by SQLite FTS5.

Embedding similarity is a soft signal: a question that literally contains a
distinctive term ("principles") can still miss the chunk containing it when
cosine scores are flat. BM25 anchors those queries on exact term matches;
retrieval fuses both rankings with Reciprocal Rank Fusion
(see app.services.retrieval).

SQLite ships with FTS5, so this index adds no dependency. Rows are keyed by
the same `{document_id}#{chunk_index}` ids Chroma uses (vector_store.py), so
both rankings fuse on identical keys and a BM25-only hit can produce a full
RetrievalResult without a second vector lookup.

Write failures raise Bm25Error: ingestion treats them as document failures
(no partial rows - index_chunks is a single transaction). Query failures are
caught in retrieve_documents and degrade to vector-only search.
"""

import logging
import re
import sqlite3
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

from app.config import get_settings
from app.schemas.documents import Chunk

logger = logging.getLogger(__name__)

# User questions are raw text; FTS5 MATCH is a query language of its own and
# rejects characters like "-" or ":". Extracting plain tokens and quoting
# them makes any input safe (porter stemming still applies per term).
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


class Bm25Error(Exception):
    """The term index could not be read or written."""


class Bm25Index:
    """FTS5-backed keyword index; one SQLite file, connection per operation."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path, timeout=10.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
                    id UNINDEXED,
                    document_id UNINDEXED,
                    filename UNINDEXED,
                    page UNINDEXED,
                    chunk_index UNINDEXED,
                    text,
                    tokenize='porter unicode61'
                )
                """
            )
        except sqlite3.Error:
            conn.close()
            raise
        return conn

    def index_chunks(self, chunks: Sequence[Chunk]) -> None:
        """Index (or re-index) chunks for their documents, atomically.

        Existing rows for the same documents are replaced in the same
        transaction, so re-ingestion and retries are idempotent and a failure
        leaves the index exactly as it was.
        """
        if not chunks:
            return
        rows = [
            (
                f"{chunk.document_id}#{chunk.chunk_index}",
                chunk.document_id,
                chunk.filename,
                chunk.page,
                chunk.chunk_index,
                chunk.text,
            )
            for chunk in chunks
        ]
        document_ids = sorted({row[1] for row in rows})
        conn = self._connect()
        try:
            with conn:
                conn.executemany(
                    "DELETE FROM chunks WHERE document_id = ?",
                    [(document_id,) for document_id in document_ids],
                )
                conn.executemany(
                    "INSERT INTO chunks (id, document_id, filename, page, "
                    "chunk_index, text) VALUES (?, ?, ?, ?, ?, ?)",
                    rows,
                )
        except sqlite3.Error as exc:
            raise Bm25Error(
                f"Failed to index {len(rows)} chunks: {type(exc).__name__}: {exc}"
            ) from exc
        finally:
            conn.close()

    def search(
        self, query: str, *, top_k: int = 5, document_id: str | None = None
    ) -> list[dict]:
        """Top-k BM25 matches, best first; [] for a term-less query."""
        terms = _TOKEN_RE.findall(query)
        if not terms:
            return []
        match = " OR ".join(f'"{term}"' for term in terms)
        sql = (
            "SELECT id, document_id, filename, page, chunk_index, text "
            "FROM chunks WHERE chunks MATCH ?"
        )
        params: list = [match]
        if document_id is not None:
            sql += " AND document_id = ?"
            params.append(document_id)
        sql += " ORDER BY bm25(chunks) LIMIT ?"
        params.append(top_k)

        conn = self._connect()
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            raise Bm25Error(f"BM25 query failed: {type(exc).__name__}: {exc}") from exc
        finally:
            conn.close()
        return [
            {
                "id": row[0],
                "document_id": row[1],
                "filename": row[2],
                "page": int(row[3]),
                "chunk_index": int(row[4]),
                "text": row[5],
            }
            for row in rows
        ]

    def delete_document(self, document_id: str) -> None:
        """Remove a document's rows; safe if none exist."""
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "DELETE FROM chunks WHERE document_id = ?", (document_id,)
                )
        except sqlite3.Error as exc:
            raise Bm25Error(
                f"Failed to remove index rows for {document_id}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        finally:
            conn.close()

    def clear(self) -> None:
        """Drop every row; used by the backfill script."""
        conn = self._connect()
        try:
            with conn:
                conn.execute("DELETE FROM chunks")
        except sqlite3.Error as exc:
            raise Bm25Error(f"Failed to clear the index: {exc}") from exc
        finally:
            conn.close()

    def count(self) -> int:
        """Indexed chunk total; used by the backfill script."""
        conn = self._connect()
        try:
            return int(conn.execute("SELECT count(*) FROM chunks").fetchone()[0])
        except sqlite3.Error as exc:
            raise Bm25Error(f"Could not count index rows: {exc}") from exc
        finally:
            conn.close()


@lru_cache(maxsize=4)
def _build_index(path: str) -> Bm25Index:
    return Bm25Index(Path(path))


def get_bm25_index() -> Bm25Index:
    """Dependency entry point; one cached index per file path."""
    settings = get_settings()
    return _build_index(str(settings.bm25_path))


def clear_bm25_cache() -> None:
    """Test hook mirroring the vector store's cache clear."""
    _build_index.cache_clear()
