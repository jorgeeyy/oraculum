import re

import tiktoken

from app.config import get_settings
from app.schemas.documents import Chunk, ExtractedPage
from app.services.text_extraction import clean_text

_ENCODING = tiktoken.get_encoding("cl100k_base")
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s")


def chunk_pages(
    pages: list[ExtractedPage],
    *,
    document_id: str,
    filename: str,
    chunk_size_tokens: int | None = None,
    chunk_overlap_tokens: int | None = None,
) -> list[Chunk]:
    """Split extracted pages into overlapping, token-bounded chunks.

    Chunks are built page by page so each chunk keeps an exact page reference
    for citations. Sentence boundaries are preferred over hard token cuts.
    """
    settings = get_settings()
    size = chunk_size_tokens if chunk_size_tokens is not None else settings.chunk_size_tokens
    overlap = (
        chunk_overlap_tokens
        if chunk_overlap_tokens is not None
        else settings.chunk_overlap_tokens
    )
    if size <= 0:
        raise ValueError("chunk_size_tokens must be positive")
    if overlap < 0 or overlap >= size:
        raise ValueError("chunk_overlap_tokens must be >= 0 and < chunk_size_tokens")

    chunks: list[Chunk] = []

    for page in pages:
        text = clean_text(page.text)
        if not text:
            continue

        sentences = _SENTENCE_BOUNDARY.split(text)
        current: list[str] = []
        current_tokens = 0

        for sentence in sentences:
            sentence_tokens = len(_ENCODING.encode(sentence))
            if sentence_tokens > size:
                if current:
                    _make_chunk(chunks, document_id, filename, page.page_number, current)
                    current, current_tokens = [], 0
                chunks.extend(
                    _split_long_sentence(
                        sentence,
                        size=size,
                        overlap=overlap,
                        document_id=document_id,
                        filename=filename,
                        page_number=page.page_number,
                        chunks_so_far=len(chunks),
                    )
                )
                continue

            if current_tokens + sentence_tokens > size and current:
                _make_chunk(chunks, document_id, filename, page.page_number, current)
                carry = _carry_overlap(current, overlap)
                current = carry
                current_tokens = len(_ENCODING.encode(" ".join(carry))) if carry else 0

            current.append(sentence)
            current_tokens += sentence_tokens

        if current:
            _make_chunk(chunks, document_id, filename, page.page_number, current)

    return chunks


def _make_chunk(
    chunks: list[Chunk],
    document_id: str,
    filename: str,
    page_number: int,
    sentences: list[str],
) -> Chunk:
    text = " ".join(sentences).strip()
    chunk = Chunk(
        document_id=document_id,
        filename=filename,
        page=page_number,
        chunk_index=len(chunks),
        token_count=len(_ENCODING.encode(text)),
        text=text,
    )
    chunks.append(chunk)
    return chunk


def _carry_overlap(sentences: list[str], overlap: int) -> list[str]:
    """Keep the trailing sentences whose tokens fit within the overlap budget."""
    if overlap <= 0 or not sentences:
        return []
    carry: list[str] = []
    tokens = 0
    for sentence in reversed(sentences):
        sentence_tokens = len(_ENCODING.encode(sentence))
        if tokens + sentence_tokens > overlap:
            break
        carry.insert(0, sentence)
        tokens += sentence_tokens
    return carry


def _split_long_sentence(
    sentence: str,
    *,
    size: int,
    overlap: int,
    document_id: str,
    filename: str,
    page_number: int,
    chunks_so_far: int,
) -> list[Chunk]:
    """Hard-split a single sentence that exceeds the chunk size."""
    tokens = _ENCODING.encode(sentence)
    results: list[Chunk] = []
    start = 0
    while start < len(tokens):
        window = tokens[start : start + size]
        text = _ENCODING.decode(window).strip()
        results.append(
            Chunk(
                document_id=document_id,
                filename=filename,
                page=page_number,
                chunk_index=chunks_so_far + len(results),
                token_count=len(window),
                text=text,
            )
        )
        if start + size >= len(tokens):
            break
        start += size - overlap
    return results
