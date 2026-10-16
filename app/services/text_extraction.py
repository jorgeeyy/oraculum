import io
from pathlib import PurePosixPath

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.schemas.documents import ExtractedPage
from app.services.errors import (
    EmptyExtractionError,
    FileTooLargeError,
    UnsupportedFileTypeError,
)

SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".md"}
_MIN_EXTRACTED_CHARS = 10


def validate_upload(*, filename: str, content_type: str | None, data: bytes, max_size: int) -> str:
    """Validate an upload and return its canonical extension.

    Checks are content-based where possible: a PDF is detected by magic bytes,
    not by trusting the client-supplied filename or content type.
    """
    if len(data) > max_size:
        raise FileTooLargeError(f"File exceeds the {max_size}-byte size limit.")

    extension = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFileTypeError(
            f"Unsupported file type {extension!r}. Supported: PDF, TXT, Markdown."
        )

    if extension == ".pdf":
        if data[:5] == b"%PDF-":
            return ".pdf"
        raise UnsupportedFileTypeError(
            "File has a .pdf extension but does not start with a %PDF header."
        )

    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnsupportedFileTypeError(
            "File content is not valid UTF-8 text; it may be binary or corrupted."
        ) from exc

    return extension


def extract_pages(filename: str, data: bytes) -> list[ExtractedPage]:
    """Extract text from the document, preserving PDF page numbers."""
    extension = PurePosixPath(filename.replace("\\", "/")).suffix.lower()

    if extension == ".pdf":
        try:
            reader = PdfReader(io.BytesIO(data))
        except PdfReadError as exc:
            raise UnsupportedFileTypeError(f"Could not read PDF: {exc}") from exc
        pages = [
            ExtractedPage(page_number=index, text=page.extract_text() or "")
            for index, page in enumerate(reader.pages, start=1)
        ]
    else:
        pages = [ExtractedPage(page_number=1, text=data.decode("utf-8"))]

    total_chars = sum(len(page.text.strip()) for page in pages)
    if total_chars < _MIN_EXTRACTED_CHARS:
        raise EmptyExtractionError(
            "No extractable text found. The document may be scanned and require OCR."
        )

    return pages


def clean_text(text: str) -> str:
    """Conservatively clean extraction artefacts without removing meaning."""
    lines = [" ".join(line.split()) for line in text.splitlines()]
    cleaned = "\n".join(lines)
    while "\n\n\n" in cleaned:
        cleaned = cleaned.replace("\n\n\n", "\n\n")
    return cleaned.strip()
