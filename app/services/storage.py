import uuid
from pathlib import Path

from app.services.errors import StoredFileMissingError


class DocumentStorage:
    """Stores raw uploaded files on disk under a safe internal UUID name.

    The client-supplied filename is never used as a filesystem path.
    """

    def __init__(self, uploads_dir: str | Path):
        self.uploads_dir = Path(uploads_dir)

    def save(self, data: bytes, extension: str) -> str:
        """Persist the file and return its document ID."""
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        document_id = str(uuid.uuid4())
        (self.uploads_dir / f"{document_id}{extension}").write_bytes(data)
        return document_id

    def load(self, document_id: str, extension: str) -> bytes:
        path = self._path(document_id, extension)
        if not path.is_file():
            raise StoredFileMissingError(f"Stored file not found for document {document_id}.")
        return path.read_bytes()

    def delete(self, document_id: str, extension: str) -> None:
        path = self._path(document_id, extension)
        if path.is_file():
            path.unlink()

    def _path(self, document_id: str, extension: str) -> Path:
        uuid.UUID(document_id)  # reject anything that is not a plain UUID
        return self.uploads_dir / f"{document_id}{extension}"
