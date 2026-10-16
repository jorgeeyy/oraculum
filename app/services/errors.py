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
