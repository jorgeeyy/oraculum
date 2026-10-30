import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from tests.test_text_extraction import make_blank_pdf, make_pdf

SAMPLE_TXT = b"# Handbook\n\nEmployees receive 20 days of annual leave per year."


def upload(client: TestClient, filename: str, data: bytes, content_type: str = "text/plain"):
    return client.post(
        "/documents",
        files={"file": (filename, data, content_type)},
    )


class TestUploadValidation:
    def test_invalid_extension_returns_400(self, client: TestClient) -> None:
        response = upload(client, "run.exe", b"MZ binary", "application/octet-stream")
        assert response.status_code == 400
        assert "Unsupported" in response.json()["detail"]

    def test_oversized_file_returns_413(self, client: TestClient) -> None:
        settings = get_settings()
        big = b"x" * (settings.max_file_size_bytes + 1)
        response = upload(client, "big.txt", big)
        assert response.status_code == 413

    @pytest.mark.skipif(
        bool(get_settings().database_url),
        reason="only applies when no database is configured",
    )
    def test_valid_upload_without_database_returns_503(self, client: TestClient) -> None:
        response = upload(client, "notes.txt", SAMPLE_TXT)
        assert response.status_code == 503


class TestDocumentLifecycle:
    def test_upload_txt_round_trip(
        self, client: TestClient, db_conn
    ) -> None:
        response = upload(client, "handbook.md", SAMPLE_TXT, "text/markdown")
        assert response.status_code == 202, response.text
        body = response.json()
        # The upload response is a snapshot taken before background ingestion.
        assert body["status"] == "pending"
        assert body["chunk_count"] is None
        assert body["file_type"] == "md"
        assert body["chunks"] == []
        document_id = body["id"]

        # TestClient runs BackgroundTasks inline, so the document is already
        # finished by the time the upload() call returns.
        detail = client.get(f"/documents/{document_id}")
        assert detail.status_code == 200
        detail_body = detail.json()
        assert detail_body["status"] == "completed"
        assert detail_body["chunk_count"] == 1
        assert detail_body["original_filename"] == "handbook.md"

        listed = client.get("/documents").json()
        assert any(d["id"] == document_id for d in listed)

        chunks = client.get(f"/documents/{document_id}/chunks")
        assert chunks.status_code == 200
        chunks_body = chunks.json()
        assert chunks_body["document_id"] == document_id
        assert chunks_body["chunk_count"] == 1
        assert chunks_body["chunk_size_tokens"] == get_settings().chunk_size_tokens
        assert "20 days" in chunks_body["chunks"][0]["text"]

        deleted = client.delete(f"/documents/{document_id}")
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True
        assert client.get(f"/documents/{document_id}").status_code == 404
        assert not (client.storage.uploads_dir / f"{document_id}.md").exists()  # type: ignore[attr-defined]

    def test_upload_pdf_with_text(
        self, client: TestClient, db_conn
    ) -> None:
        data = make_pdf("Annual leave policy: employees receive 20 days of paid leave.")
        response = upload(client, "handbook.pdf", data, "application/pdf")
        assert response.status_code == 202, response.text
        assert response.json()["status"] == "pending"
        document_id = response.json()["id"]

        detail = client.get(f"/documents/{document_id}").json()
        assert detail["status"] == "completed"
        assert detail["file_type"] == "pdf"
        chunks = client.get(f"/documents/{document_id}/chunks").json()
        assert "20 days" in chunks["chunks"][0]["text"]

    def test_pdf_without_text_marks_failed(
        self, client: TestClient, db_conn
    ) -> None:
        response = upload(client, "scan.pdf", make_blank_pdf(), "application/pdf")
        assert response.status_code == 202
        document_id = response.json()["id"]
        detail = client.get(f"/documents/{document_id}")
        assert detail.status_code == 200
        record = detail.json()
        assert record["status"] == "failed"
        assert "OCR" in record["error_message"]

    def test_unknown_document_returns_404(self, client: TestClient, db_conn) -> None:
        assert client.get(f"/documents/{uuid.uuid4()}").status_code == 404

    def test_missing_stored_file_returns_410(
        self, client: TestClient, db_conn
    ) -> None:
        response = upload(client, "handbook.md", SAMPLE_TXT, "text/markdown")
        assert response.status_code == 202
        document_id = response.json()["id"]

        # simulate an orphaned record: file disappears from disk
        (client.storage.uploads_dir / f"{document_id}.md").unlink()  # type: ignore[attr-defined]

        chunks = client.get(f"/documents/{document_id}/chunks")
        assert chunks.status_code == 410
        assert "Re-upload" in chunks.json()["detail"]

    def test_malformed_id_returns_404(self, client: TestClient) -> None:
        assert client.get("/documents/not-a-uuid").status_code == 404

    def test_rechunk_uses_current_config(
        self, client: TestClient, db_conn, monkeypatch
    ) -> None:
        long_text = (
            "The annual leave policy grants twenty days of paid leave each year. "
            "Requests go through the HR portal. Carryover is capped at five days. "
        ) * 10
        response = upload(client, "handbook.txt", long_text.encode())
        assert response.status_code == 202
        document_id = response.json()["id"]
        baseline = client.get(f"/documents/{document_id}/chunks").json()
        assert baseline["chunk_count"] == 1

        monkeypatch.setenv("CHUNK_SIZE_TOKENS", "50")
        monkeypatch.setenv("CHUNK_OVERLAP_TOKENS", "10")
        get_settings.cache_clear()
        try:
            smaller = client.get(f"/documents/{document_id}/chunks").json()
        finally:
            monkeypatch.delenv("CHUNK_SIZE_TOKENS")
            monkeypatch.delenv("CHUNK_OVERLAP_TOKENS")
            get_settings.cache_clear()

        assert smaller["chunk_count"] > baseline["chunk_count"]
        assert smaller["chunk_size_tokens"] == 50


class TestDocumentContent:
    def test_content_returns_full_original_text(
        self, client: TestClient, db_conn
    ) -> None:
        response = upload(client, "handbook.md", SAMPLE_TXT, "text/markdown")
        assert response.status_code == 202
        document_id = response.json()["id"]

        content = client.get(f"/documents/{document_id}/content")
        assert content.status_code == 200
        body = content.json()
        assert body["document_id"] == document_id
        assert body["filename"] == "handbook.md"
        assert body["file_type"] == "md"
        assert body["page_count"] == 1
        assert body["text"] == SAMPLE_TXT.decode()

    def test_content_pdf_reports_pages(
        self, client: TestClient, db_conn
    ) -> None:
        data = make_pdf("Annual leave policy: employees receive 20 days of paid leave.")
        response = upload(client, "handbook.pdf", data, "application/pdf")
        assert response.status_code == 202
        document_id = response.json()["id"]

        content = client.get(f"/documents/{document_id}/content")
        assert content.status_code == 200
        body = content.json()
        assert body["page_count"] >= 1
        assert "20 days" in body["text"]

    def test_content_unknown_document_returns_404(self, client: TestClient) -> None:
        assert client.get(f"/documents/{uuid.uuid4()}/content").status_code == 404

    def test_content_missing_stored_file_returns_410(
        self, client: TestClient, db_conn
    ) -> None:
        response = upload(client, "handbook.md", SAMPLE_TXT, "text/markdown")
        assert response.status_code == 202
        document_id = response.json()["id"]
        (client.storage.uploads_dir / f"{document_id}.md").unlink()

        content = client.get(f"/documents/{document_id}/content")
        assert content.status_code == 410
        assert "Re-upload" in content.json()["detail"]


class TestDocumentFile:
    def test_file_serves_raw_pdf(
        self, client: TestClient, db_conn
    ) -> None:
        data = make_pdf("Annual leave policy: employees receive 20 days of paid leave.")
        response = upload(client, "handbook.pdf", data, "application/pdf")
        assert response.status_code == 202
        document_id = response.json()["id"]

        file_response = client.get(f"/documents/{document_id}/file")
        assert file_response.status_code == 200
        assert file_response.headers["content-type"].startswith("application/pdf")
        assert file_response.content[:5] == b"%PDF-"

    def test_file_serves_raw_markdown(
        self, client: TestClient, db_conn
    ) -> None:
        response = upload(client, "handbook.md", SAMPLE_TXT, "text/markdown")
        assert response.status_code == 202
        document_id = response.json()["id"]

        file_response = client.get(f"/documents/{document_id}/file")
        assert file_response.status_code == 200
        assert "text/markdown" in file_response.headers["content-type"]
        assert file_response.content == SAMPLE_TXT

    def test_file_unknown_document_returns_404(self, client: TestClient) -> None:
        assert client.get(f"/documents/{uuid.uuid4()}/file").status_code == 404

    def test_file_missing_stored_file_returns_410(
        self, client: TestClient, db_conn
    ) -> None:
        response = upload(client, "handbook.md", SAMPLE_TXT, "text/markdown")
        assert response.status_code == 202
        document_id = response.json()["id"]
        (client.storage.uploads_dir / f"{document_id}.md").unlink()

        assert client.get(f"/documents/{document_id}/file").status_code == 410


class TestIndexing:
    def test_upload_embeds_and_indexes_chunks(
        self,
        client: TestClient,
        db_conn,
        fake_embedder,
        fake_vector_store,
    ) -> None:
        response = upload(client, "handbook.md", SAMPLE_TXT, "text/markdown")
        assert response.status_code == 202, response.text
        document_id = response.json()["id"]
        assert response.json()["status"] == "pending"

        # provenance is recorded on the document after background ingestion
        detail = client.get(f"/documents/{document_id}").json()
        assert detail["status"] == "completed"
        assert detail["embedding_model"] == "test-embedding-model"
        assert detail["chunk_size_tokens"] == get_settings().chunk_size_tokens
        assert detail["chunk_overlap_tokens"] == get_settings().chunk_overlap_tokens

        # the embedder saw the exact chunk texts
        chunks_body = client.get(f"/documents/{document_id}/chunks").json()
        chunk_texts = [c["text"] for c in chunks_body["chunks"]]
        assert fake_embedder.batches == [chunk_texts]

        # the store received one vector per chunk, with matching citations
        assert len(fake_vector_store.upserts) == 1
        chunks, embeddings = fake_vector_store.upserts[0]
        assert len(chunks) == len(embeddings) == detail["chunk_count"]
        assert chunks[0].document_id == document_id

    def test_embedding_failure_marks_document_failed(
        self, client: TestClient, db_conn, fake_embedder
    ) -> None:
        from app.services.errors import EmbeddingServiceError

        def boom(texts):
            raise EmbeddingServiceError("Embedding request failed after 3 attempts.")

        fake_embedder.embed = boom  # type: ignore[method-assign]

        response = upload(client, "handbook.md", SAMPLE_TXT, "text/markdown")
        assert response.status_code == 202
        document_id = response.json()["id"]

        record = client.get(f"/documents/{document_id}").json()
        assert record["status"] == "failed"
        assert "after 3 attempts" in record["error_message"]
        assert record["embedding_model"] is None

    def test_vector_upsert_failure_marks_document_failed(
        self, client: TestClient, db_conn, fake_vector_store
    ) -> None:
        from app.services.errors import VectorStoreError

        fake_vector_store.fail_upsert = VectorStoreError("index write failed")

        response = upload(client, "handbook.md", SAMPLE_TXT, "text/markdown")
        assert response.status_code == 202
        document_id = response.json()["id"]

        record = client.get(f"/documents/{document_id}").json()
        assert record["status"] == "failed"
        assert "index write failed" in record["error_message"]

    def test_delete_removes_vectors_before_record(
        self,
        client: TestClient,
        db_conn,
        fake_vector_store,
    ) -> None:
        response = upload(client, "handbook.md", SAMPLE_TXT, "text/markdown")
        document_id = response.json()["id"]

        deleted = client.delete(f"/documents/{document_id}")
        assert deleted.status_code == 200
        assert fake_vector_store.deletes == [document_id]
        assert client.get(f"/documents/{document_id}").status_code == 404

    def test_delete_vector_failure_keeps_document_retryable(
        self, client: TestClient, db_conn, fake_vector_store
    ) -> None:
        from app.services.errors import VectorStoreError

        response = upload(client, "handbook.md", SAMPLE_TXT, "text/markdown")
        document_id = response.json()["id"]

        fake_vector_store.fail_delete = VectorStoreError("chroma locked")
        failed = client.delete(f"/documents/{document_id}")
        assert failed.status_code == 503
        # nothing was removed: the record survives for a retry
        assert client.get(f"/documents/{document_id}").status_code == 200

        fake_vector_store.fail_delete = None
        retried = client.delete(f"/documents/{document_id}")
        assert retried.status_code == 200
        assert fake_vector_store.deletes == [document_id]
        assert client.get(f"/documents/{document_id}").status_code == 404


PASSWORD = "correct-horse-battery"


def _db():
    from sqlalchemy.orm import sessionmaker

    from app.db.session import get_engine

    return sessionmaker(bind=get_engine(), expire_on_commit=False)()


def _register(client: TestClient, email: str) -> None:
    response = client.post(
        "/auth/register", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 201, response.text


def _promote_to_admin(email: str) -> None:
    from app.db import repositories

    session = _db()
    try:
        user = repositories.get_user_by_email(session, email)
        assert user is not None
        user.role = "admin"
        session.commit()
    finally:
        session.close()


def _create_shared_document(document_id: str, filename: str = "shared.txt") -> None:
    """A NULL-owner document: CLI/script ingestion, visible to everyone."""
    from app.db import repositories

    session = _db()
    try:
        repositories.create_document(
            session,
            document_id=document_id,
            original_filename=filename,
            file_type="txt",
            size_bytes=10,
        )
    finally:
        session.close()


class TestDocumentOwnerScoping:
    """Uploads are private to the account that made them (user-reported fix).

    A signed-in user sees the shared corpus (NULL owner: CLI ingestion,
    pre-identity uploads) plus their own uploads - never another user's.
    Admin sessions and API-key callers keep the full operational view.
    """

    def test_upload_is_private_to_the_uploader(
        self, client_with_settings, db_conn
    ) -> None:
        alice = client_with_settings()
        _register(alice, "alice-scoping@local.dev")
        bob = client_with_settings()
        _register(bob, "bob-scoping@local.dev")

        document_id = upload(alice, "alice-notes.txt", SAMPLE_TXT).json()["id"]

        # The uploader keeps full access.
        assert alice.get(f"/documents/{document_id}").status_code == 200
        listed_alice = [d["id"] for d in alice.get("/documents").json()]
        assert document_id in listed_alice

        # The other user cannot see it anywhere - list, detail, chunks,
        # content, raw file, and delete all treat it as missing.
        listed_bob = [d["id"] for d in bob.get("/documents").json()]
        assert document_id not in listed_bob
        assert bob.get(f"/documents/{document_id}").status_code == 404
        assert bob.get(f"/documents/{document_id}/chunks").status_code == 404
        assert bob.get(f"/documents/{document_id}/content").status_code == 404
        assert bob.get(f"/documents/{document_id}/file").status_code == 404
        assert bob.delete(f"/documents/{document_id}").status_code == 404
        # The failed delete changed nothing for the real owner.
        assert alice.get(f"/documents/{document_id}").status_code == 200

    def test_shared_corpus_stays_visible_to_everyone(
        self, client_with_settings, db_conn
    ) -> None:
        shared_id = str(uuid.uuid4())
        _create_shared_document(shared_id)

        alice = client_with_settings()
        _register(alice, "alice-shared@local.dev")
        bob = client_with_settings()
        _register(bob, "bob-shared@local.dev")

        assert shared_id in [d["id"] for d in alice.get("/documents").json()]
        assert shared_id in [d["id"] for d in bob.get("/documents").json()]
        assert bob.get(f"/documents/{shared_id}").status_code == 200

    def test_identical_bytes_from_two_users_create_separate_documents(
        self, client_with_settings, db_conn
    ) -> None:
        # Dedup is owner-scoped: the same file uploaded by two users must
        # not collapse into one row (which would leak one user's document
        # to the other through the duplicate response).
        alice = client_with_settings()
        _register(alice, "alice-dedup@local.dev")
        bob = client_with_settings()
        _register(bob, "bob-dedup@local.dev")

        from_a = upload(alice, "policy.txt", SAMPLE_TXT).json()
        from_b = upload(bob, "policy.txt", SAMPLE_TXT).json()

        assert from_a["id"] != from_b["id"]
        assert from_b["id"] not in [d["id"] for d in alice.get("/documents").json()]
        assert from_a["id"] not in [d["id"] for d in bob.get("/documents").json()]

    def test_same_user_reupload_still_deduplicates(
        self, client_with_settings, db_conn
    ) -> None:
        alice = client_with_settings()
        _register(alice, "alice-dedup2@local.dev")

        first = upload(alice, "policy.txt", SAMPLE_TXT)
        assert first.status_code == 202
        again = upload(alice, "policy.txt", SAMPLE_TXT)
        assert again.status_code == 200  # duplicate short-circuit
        assert again.json()["id"] == first.json()["id"]

    def test_admin_session_sees_every_document(
        self, client_with_settings, db_conn
    ) -> None:
        alice = client_with_settings()
        _register(alice, "alice-admin-view@local.dev")
        document_id = upload(alice, "private.txt", SAMPLE_TXT).json()["id"]

        admin = client_with_settings()
        _register(admin, "boss-view@local.dev")
        _promote_to_admin("boss-view@local.dev")

        listed = [d["id"] for d in admin.get("/documents").json()]
        assert document_id in listed
        assert admin.get(f"/documents/{document_id}").status_code == 200

    def test_foreign_document_id_is_404_for_retrieve_and_chat(
        self, client_with_settings, db_conn
    ) -> None:
        alice = client_with_settings()
        _register(alice, "alice-foreign@local.dev")
        document_id = upload(alice, "only-alice.txt", SAMPLE_TXT).json()["id"]

        bob = client_with_settings()
        _register(bob, "bob-foreign@local.dev")

        assert (
            bob.post(
                "/retrieve", json={"question": "leave?", "document_id": document_id}
            ).status_code
            == 404
        )
        assert (
            bob.post(
                "/chat", json={"question": "leave?", "document_id": document_id}
            ).status_code
            == 404
        )

    def test_retrieval_only_searches_visible_documents(
        self, client_with_settings, fake_vector_store, db_conn
    ) -> None:
        alice = client_with_settings()
        _register(alice, "alice-retrieve@local.dev")
        doc_a = upload(alice, "alice.txt", SAMPLE_TXT).json()["id"]

        bob = client_with_settings()
        _register(bob, "bob-retrieve@local.dev")
        doc_b = upload(bob, "bob.txt", b"# Bob\n\nContractors get 10 days.").json()["id"]

        assert bob.post("/retrieve", json={"question": "days?"}).status_code == 200
        visible_bob = fake_vector_store.queries[0]["document_ids"]
        assert doc_b in visible_bob
        assert doc_a not in visible_bob  # alice's upload never leaks in

        assert alice.post("/retrieve", json={"question": "days?"}).status_code == 200
        visible_alice = fake_vector_store.queries[1]["document_ids"]
        assert doc_a in visible_alice
        assert doc_b not in visible_alice

    def test_chat_retrieval_only_searches_visible_documents(
        self, client_with_settings, fake_vector_store, db_conn
    ) -> None:
        alice = client_with_settings()
        _register(alice, "alice-chat-scope@local.dev")
        doc_a = upload(alice, "alice.txt", SAMPLE_TXT).json()["id"]

        bob = client_with_settings()
        _register(bob, "bob-chat-scope@local.dev")
        doc_b = upload(bob, "bob.txt", b"# Bob\n\nContractors get 10 days.").json()["id"]

        response = bob.post("/chat", json={"question": "How many days?"})
        assert response.status_code == 200
        visible = fake_vector_store.queries[0]["document_ids"]
        assert doc_b in visible
        assert doc_a not in visible  # alice's upload never leaks into bob's answers

    def test_user_without_uploads_sees_only_shared_corpus(
        self, client_with_settings, fake_vector_store, db_conn
    ) -> None:
        # Self-contained: the shared corpus is seeded here, never assumed
        # from the dev database (which may be freshly flushed). A fresh
        # account's visible set = shared corpus (NULL owner) only; other
        # users' uploads are never part of it.
        shared_id = str(uuid.uuid4())
        _create_shared_document(shared_id)

        alice = client_with_settings()
        _register(alice, "alice-noshared@local.dev")
        doc_a = upload(alice, "alice.txt", SAMPLE_TXT).json()["id"]

        carol = client_with_settings()
        _register(carol, "carol-noshared@local.dev")

        assert carol.post("/retrieve", json={"question": "days?"}).status_code == 200
        visible = fake_vector_store.queries[0]["document_ids"]
        assert shared_id in visible  # shared corpus stays visible to everyone
        assert doc_a not in visible  # but alice's private upload never leaks

    def test_admin_retrieval_is_unscoped(
        self, client_with_settings, fake_vector_store, db_conn
    ) -> None:
        admin = client_with_settings()
        _register(admin, "boss-retrieve@local.dev")
        _promote_to_admin("boss-retrieve@local.dev")

        assert admin.post("/retrieve", json={"question": "days?"}).status_code == 200
        # None = no document filter: the whole corpus, as before identity.
        assert fake_vector_store.queries[0]["document_ids"] is None
