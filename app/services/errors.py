class IngestionError(Exception):
    """Base class for ingestion-related failures."""


class UnsupportedFileTypeError(IngestionError):
    """The uploaded file is not a supported type or failed content validation."""


class FileTooLargeError(IngestionError):
    """The uploaded file exceeds the configured size limit."""


class EmptyExtractionError(IngestionError):
    """No extractable text was found; the document may require OCR."""


class DatabaseNotConfiguredError(Exception):
    """DATABASE_URL is missing or the database is unreachable."""


class StoredFileMissingError(Exception):
    """A document record exists but its stored file is missing on disk."""


class EmbeddingNotConfiguredError(Exception):
    """OPENAI_API_KEY is missing or the embedding model is unavailable."""


class EmbeddingServiceError(Exception):
    """The embedding API failed after exhausting bounded retries."""


class VectorStoreError(Exception):
    """A vector store operation failed (indexing, querying, or deletion)."""
