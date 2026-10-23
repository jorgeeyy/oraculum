"""Unit/integration tests for the background ingestion pipeline.

These call `ingest_document` / `requeue_interrupted_ingestions` directly (the
API-level 202 contract is covered in test_documents_api.py). TestClient runs
BackgroundTasks inline, so pending-state windows are seeded via the repos.
"""

import hashlib
import threading
import time
import uuid

import pytest
from sqlalchemy.orm import sessionmaker

from app.db import repositories
from app.services import ingestion
from app.services.errors import EmbeddingServiceError
from app.services.storage import DocumentStorage

SAMPLE = b"# Handbook\n\nEmployees receive 20 days of annual leave per year."


def _session(db_conn):
    return sessionmaker(bind=db_conn, expire_on_commit=False)()


def _seed_document(
    db_conn,
    storage,
    *,
    data: bytes = SAMPLE,
    filename: str = "handbook.md",
    status: str = "pending",
    save_file: bool = True,
) -> str:
    """Insert a document row (and its stored file) without running ingestion."""
    extension = ".pdf" if filename.endswith(".pdf") else ".md"
    document_id = (
        storage.save(data, extension) if save_file else str(uuid.uuid4())
    )
    session = _session(db_conn)
    try:
        repositories.create_document(
            session,
            document_id=document_id,
            original_filename=filename,
            file_type=extension.lstrip("."),
            size_bytes=len(data),
            content_hash=hashlib.sha256(data).hexdigest(),
        )
        if status == "completed":
            repositories.complete_document(
                session,
                document_id,
                1,
                embedding_model="seed",
                chunk_size_tokens=100,
                chunk_overlap_tokens=10,
            )
        elif status == "failed":
            repositories.fail_document(session, document_id, "seed failure")
    finally:
        session.close()
    return document_id


def _get_status(db_conn, document_id: str):
    session = _session(db_conn)
    try:
        record = repositories.get_document(session, document_id)
        return record.status if record else None, record
    finally:
        session.close()


class TestIngestDocument:
    def test_missing_document_is_a_noop(
        self, db_conn, fake_embedder, fake_vector_store, tmp_path
    ) -> None:
        ingestion.ingest_document(
            str(uuid.uuid4()),
            embedder=fake_embedder,
            store=fake_vector_store,
            storage=DocumentStorage(tmp_path / "uploads"),
        )
        assert fake_embedder.batches == []
        assert fake_vector_store.upserts == []

    def test_completed_document_is_not_reingested(
        self, db_conn, fake_embedder, fake_vector_store, tmp_path
    ) -> None:
        storage = DocumentStorage(tmp_path / "uploads")
        document_id = _seed_document(
            db_conn, storage, status="completed"
        )

        ingestion.ingest_document(
            document_id,
            embedder=fake_embedder,
            store=fake_vector_store,
            storage=storage,
        )
        assert fake_embedder.batches == []
        assert fake_vector_store.upserts == []
        status, _ = _get_status(db_conn, document_id)
        assert status == "completed"

    def test_successful_ingest_completes_document(
        self, db_conn, fake_embedder, fake_vector_store, tmp_path
    ) -> None:
        storage = DocumentStorage(tmp_path / "uploads")
        document_id = _seed_document(db_conn, storage)

        ingestion.ingest_document(
            document_id,
            embedder=fake_embedder,
            store=fake_vector_store,
            storage=storage,
        )

        status, record = _get_status(db_conn, document_id)
        assert status == "completed"
        assert record.chunk_count == 1
        assert record.embedding_model == "test-embedding-model"
        assert len(fake_vector_store.upserts) == 1

    def test_missing_stored_file_fails_document(
        self, db_conn, fake_embedder, fake_vector_store, tmp_path
    ) -> None:
        storage = DocumentStorage(tmp_path / "uploads")
        document_id = _seed_document(db_conn, storage, save_file=False)

        ingestion.ingest_document(
            document_id,
            embedder=fake_embedder,
            store=fake_vector_store,
            storage=storage,
        )

        status, record = _get_status(db_conn, document_id)
        assert status == "failed"
        assert "Stored file is missing" in record.error_message
        assert fake_embedder.batches == []

    def test_embedding_failure_marks_failed(
        self, db_conn, fake_vector_store, tmp_path
    ) -> None:
        class BoomEmbedder:
            model = "boom"

            def embed(self, texts):
                raise EmbeddingServiceError("Embedding request failed after 3 attempts.")

        storage = DocumentStorage(tmp_path / "uploads")
        document_id = _seed_document(db_conn, storage)

        ingestion.ingest_document(
            document_id,
            embedder=BoomEmbedder(),
            store=fake_vector_store,
            storage=storage,
        )

        status, record = _get_status(db_conn, document_id)
        assert status == "failed"
        assert "after 3 attempts" in record.error_message
        assert record.embedding_model is None
        assert fake_vector_store.upserts == []

    def test_document_deleted_mid_flight_cleans_vectors(
        self, db_conn, fake_vector_store, tmp_path
    ) -> None:
        storage = DocumentStorage(tmp_path / "uploads")
        document_id = _seed_document(db_conn, storage)

        class DeletingEmbedder:
            model = "deleting"

            def embed(self, texts):
                session = _session(db_conn)
                try:
                    repositories.delete_document(session, document_id)
                finally:
                    session.close()
                return [[0.1, 0.2] for _ in texts]

        ingestion.ingest_document(
            document_id,
            embedder=DeletingEmbedder(),
            store=fake_vector_store,
            storage=storage,
        )

        assert fake_vector_store.upserts == []
        assert fake_vector_store.deletes == [document_id]
        status, _ = _get_status(db_conn, document_id)
        assert status is None

    def test_concurrent_ingests_are_serialized(
        self, db_conn, fake_vector_store, tmp_path
    ) -> None:
        storage = DocumentStorage(tmp_path / "uploads")
        id_a = _seed_document(db_conn, storage, data=b"# A\n\nalpha")
        id_b = _seed_document(db_conn, storage, data=b"# B\n\nbravo")

        class SlowEmbedder:
            model = "slow"

            def __init__(self) -> None:
                self.active = 0
                self.max_active = 0
                self._lock = threading.Lock()

            def embed(self, texts):
                with self._lock:
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                time.sleep(0.05)
                with self._lock:
                    self.active -= 1
                return [[0.1, 0.2] for _ in texts]

        embedder = SlowEmbedder()
        threads = [
            threading.Thread(
                target=ingestion.ingest_document,
                args=(doc_id,),
                kwargs={
                    "embedder": embedder,
                    "store": fake_vector_store,
                    "storage": storage,
                },
            )
            for doc_id in (id_a, id_b)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        assert embedder.max_active == 1
        assert _get_status(db_conn, id_a)[0] == "completed"
        assert _get_status(db_conn, id_b)[0] == "completed"


class TestUploadDedupWhileActive:
    def test_reupload_while_pending_returns_existing_record(
        self, client, db_conn, fake_embedder, fake_vector_store
    ) -> None:
        document_id = _seed_document(db_conn, client.storage)

        response = client.post(
            "/documents",
            files={"file": ("renamed.md", SAMPLE, "text/markdown")},
        )
        assert response.status_code == 202, response.text
        body = response.json()
        assert body["id"] == document_id
        assert body["status"] == "pending"
        # No second ingestion job was queued for the same bytes.
        assert fake_embedder.batches == []
        assert fake_vector_store.upserts == []

    def test_failed_reupload_reuses_row_and_retries(
        self, client, db_conn, fake_embedder, fake_vector_store
    ) -> None:
        from tests.test_text_extraction import make_blank_pdf

        blank = make_blank_pdf()
        first = client.post(
            "/documents",
            files={"file": ("scan.pdf", blank, "application/pdf")},
        )
        assert first.status_code == 202
        document_id = first.json()["id"]
        assert _get_status(db_conn, document_id)[0] == "failed"

        second = client.post(
            "/documents",
            files={"file": ("scan_again.pdf", blank, "application/pdf")},
        )
        assert second.status_code == 202, second.text
        assert second.json()["id"] == document_id  # same row, not a new one
        status, record = _get_status(db_conn, document_id)
        assert status == "failed"
        assert "OCR" in record.error_message


class TestRepositoryTransitions:
    def test_mark_processing_clears_stale_error(self, db_conn, tmp_path) -> None:
        storage = DocumentStorage(tmp_path / "uploads")
        document_id = _seed_document(db_conn, storage, status="failed")
        session = _session(db_conn)
        try:
            repositories.mark_processing(session, document_id)
        finally:
            session.close()
        status, record = _get_status(db_conn, document_id)
        assert status == "processing"
        assert record.error_message is None

    def test_reopen_failed_document_resets_to_pending(
        self, db_conn, tmp_path
    ) -> None:
        storage = DocumentStorage(tmp_path / "uploads")
        document_id = _seed_document(db_conn, storage, status="failed")
        session = _session(db_conn)
        try:
            repositories.reopen_document(
                session,
                document_id,
                original_filename="retry.md",
                size_bytes=42,
            )
        finally:
            session.close()
        status, record = _get_status(db_conn, document_id)
        assert status == "pending"
        assert record.error_message is None
        assert record.original_filename == "retry.md"
        assert record.size_bytes == 42
        assert record.chunk_count is None


class TestStartupRecovery:
    def test_requeue_finishes_interrupted_documents(
        self, client, db_conn, fake_embedder, fake_vector_store, monkeypatch
    ) -> None:
        document_id = _seed_document(db_conn, client.storage)
        storage = client.storage

        monkeypatch.setattr(ingestion, "build_embedder", lambda settings: fake_embedder)
        monkeypatch.setattr(ingestion, "get_vector_store", lambda: fake_vector_store)
        monkeypatch.setattr(ingestion, "DocumentStorage", lambda path: storage)

        ingestion.requeue_interrupted_ingestions()

        status, record = _get_status(db_conn, document_id)
        assert status == "completed"
        assert record.embedding_model == "test-embedding-model"
        assert len(fake_vector_store.upserts) == 1

    def test_requeue_with_nothing_pending_is_a_noop(
        self, fake_embedder, fake_vector_store, monkeypatch
    ) -> None:
        monkeypatch.setattr(ingestion, "build_embedder", lambda settings: fake_embedder)
        monkeypatch.setattr(ingestion, "get_vector_store", lambda: fake_vector_store)

        ingestion.requeue_interrupted_ingestions()  # must not raise
        assert fake_embedder.batches == []
        assert fake_vector_store.upserts == []
