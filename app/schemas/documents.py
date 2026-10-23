from datetime import datetime

from pydantic import BaseModel, Field


class ExtractedPage(BaseModel):
    page_number: int
    text: str


class Chunk(BaseModel):
    document_id: str
    filename: str
    page: int
    chunk_index: int
    token_count: int
    text: str


class DocumentRecord(BaseModel):
    id: str
    original_filename: str
    file_type: str
    size_bytes: int
    chunk_count: int | None = None
    status: str
    error_message: str | None = None
    embedding_model: str | None = None
    chunk_size_tokens: int | None = None
    chunk_overlap_tokens: int | None = None
    content_hash: str | None = None
    created_at: datetime


class UploadDocumentResponse(DocumentRecord):
    chunks: list[Chunk] = Field(default_factory=list)


class ChunksResponse(BaseModel):
    document_id: str
    chunk_size_tokens: int
    chunk_overlap_tokens: int
    chunk_count: int
    chunks: list[Chunk]


class DocumentContentResponse(BaseModel):
    document_id: str
    filename: str
    file_type: str
    page_count: int
    text: str


class DeleteResponse(BaseModel):
    deleted: bool
    document_id: str
