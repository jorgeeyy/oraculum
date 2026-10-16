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
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["status"] == "completed"
        assert body["chunk_count"] == 1
        assert body["file_type"] == "md"
        assert len(body["chunks"]) == 1
        assert "20 days" in body["chunks"][0]["text"]
        document_id = body["id"]

        listed = client.get("/documents").json()
        assert any(d["id"] == document_id for d in listed)

        detail = client.get(f"/documents/{document_id}")
        assert detail.status_code == 200
        assert detail.json()["original_filename"] == "handbook.md"

        chunks = client.get(f"/documents/{document_id}/chunks")
        assert chunks.status_code == 200
        chunks_body = chunks.json()
        assert chunks_body["document_id"] == document_id
        assert chunks_body["chunk_count"] == 1
        assert chunks_body["chunk_size_tokens"] == get_settings().chunk_size_tokens

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
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["status"] == "completed"
        assert body["file_type"] == "pdf"
        assert "20 days" in body["chunks"][0]["text"]

    def test_pdf_without_text_marks_failed(
        self, client: TestClient, db_conn
    ) -> None:
        response = upload(client, "scan.pdf", make_blank_pdf(), "application/pdf")
        assert response.status_code == 422
        document_id = response.json()["detail"]["document_id"]
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
        assert response.status_code == 201
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
        assert response.status_code == 201
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
