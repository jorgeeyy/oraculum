import pytest

from app.schemas.documents import ExtractedPage
from app.services.chunking import chunk_pages

DOC_ID = "11111111-2222-3333-4444-555555555555"
LONG_TEXT = (
    "The annual leave policy grants employees twenty days of paid leave each year. "
    "Leave must be requested through the HR portal in advance. "
    "Part-time employees receive leave pro rata based on contracted hours. "
    "Unused leave may carry over up to five days. "
    "Carry-over days expire at the end of March. "
    "Sick leave is recorded separately from annual leave. "
    "Employees must notify their manager before 9:30am when unwell. "
    "Expenses must be submitted within thirty days of the spend. "
    "Receipts are required for claims over twenty-five pounds. "
    "Remote working is allowed up to three days per week by agreement. "
) * 4


class TestChunking:
    def test_short_text_single_chunk(self) -> None:
        pages = [ExtractedPage(page_number=1, text="Annual leave is 20 days per year.")]
        chunks = chunk_pages(
            pages, document_id=DOC_ID, filename="handbook.md",
            chunk_size_tokens=600, chunk_overlap_tokens=80,
        )
        assert len(chunks) == 1
        chunk = chunks[0]
        assert chunk.document_id == DOC_ID
        assert chunk.filename == "handbook.md"
        assert chunk.page == 1
        assert chunk.chunk_index == 0
        assert "20 days" in chunk.text
        assert 0 < chunk.token_count <= 600

    def test_long_text_produces_overlapping_chunks(self) -> None:
        pages = [ExtractedPage(page_number=1, text=LONG_TEXT)]
        chunks = chunk_pages(
            pages, document_id=DOC_ID, filename="handbook.md",
            chunk_size_tokens=100, chunk_overlap_tokens=20,
        )
        assert len(chunks) > 1
        for chunk in chunks:
            assert chunk.token_count <= 100
            assert chunk.text
        assert [c.chunk_index for c in chunks] == list(range(len(chunks)))

    def test_page_metadata_preserved(self) -> None:
        pages = [
            ExtractedPage(page_number=1, text="Page one content about leave policies."),
            ExtractedPage(page_number=2, text="Page two content about expenses policy."),
        ]
        chunks = chunk_pages(
            pages, document_id=DOC_ID, filename="handbook.md",
            chunk_size_tokens=600, chunk_overlap_tokens=80,
        )
        assert len(chunks) == 2
        assert chunks[0].page == 1
        assert chunks[1].page == 2
        assert "leave" in chunks[0].text
        assert "expenses" in chunks[1].text

    def test_empty_pages_yield_no_chunks(self) -> None:
        pages = [ExtractedPage(page_number=1, text="   \n  ")]
        chunks = chunk_pages(
            pages, document_id=DOC_ID, filename="handbook.md",
            chunk_size_tokens=600, chunk_overlap_tokens=80,
        )
        assert chunks == []

    def test_deterministic_output(self) -> None:
        pages = [ExtractedPage(page_number=1, text=LONG_TEXT)]
        kwargs = dict(
            document_id=DOC_ID, filename="handbook.md",
            chunk_size_tokens=150, chunk_overlap_tokens=30,
        )
        first = chunk_pages(pages, **kwargs)
        second = chunk_pages(pages, **kwargs)
        assert first == second

    def test_invalid_overlap_rejected(self) -> None:
        pages = [ExtractedPage(page_number=1, text="Some text.")]
        with pytest.raises(ValueError, match="overlap"):
            chunk_pages(
                pages, document_id=DOC_ID, filename="a.txt",
                chunk_size_tokens=10, chunk_overlap_tokens=10,
            )

    def test_huge_single_sentence_is_split(self) -> None:
        pages = [ExtractedPage(page_number=1, text="word " * 500)]
        chunks = chunk_pages(
            pages, document_id=DOC_ID, filename="a.txt",
            chunk_size_tokens=50, chunk_overlap_tokens=10,
        )
        assert len(chunks) > 1
        assert all(c.token_count <= 50 for c in chunks)
