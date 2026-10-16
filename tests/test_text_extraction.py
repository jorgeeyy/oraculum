import io

import pytest
from pypdf import PdfWriter

from app.services.errors import (
    EmptyExtractionError,
    FileTooLargeError,
    UnsupportedFileTypeError,
)
from app.services.text_extraction import extract_pages, validate_upload


def make_pdf(text: str) -> bytes:
    """Build a small single-page PDF with extractable text."""
    from pypdf.generic import (
        DecodedStreamObject,
        DictionaryObject,
        NameObject,
        NumberObject,
    )

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)

    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): writer._add_object(font)}  # noqa: SLF001
            )
        }
    )

    stream = DecodedStreamObject()
    stream.set_data(
        f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1", errors="replace")
    )
    page[NameObject("/Contents")] = writer._add_object(stream)  # noqa: SLF001
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def make_blank_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


class TestValidateUpload:
    def test_valid_txt(self) -> None:
        assert validate_upload(
            filename="notes.txt", content_type="text/plain", data=b"hello", max_size=1000
        ) == ".txt"

    def test_valid_markdown(self) -> None:
        assert validate_upload(
            filename="notes.md", content_type="text/markdown", data=b"# hi", max_size=1000
        ) == ".md"

    def test_valid_pdf_by_magic_bytes(self) -> None:
        data = b"%PDF-1.4\nwhatever"
        assert validate_upload(
            filename="doc.pdf", content_type="application/pdf", data=data, max_size=1000
        ) == ".pdf"

    def test_invalid_extension(self) -> None:
        with pytest.raises(UnsupportedFileTypeError):
            validate_upload(
                filename="run.exe", content_type="application/octet-stream", data=b"MZ", max_size=1000
            )

    def test_fake_pdf_extension_with_binary_content(self) -> None:
        with pytest.raises(UnsupportedFileTypeError):
            validate_upload(
                filename="fake.pdf", content_type="application/pdf", data=b"\x00\x01\x02", max_size=1000
            )

    def test_oversized_file(self) -> None:
        with pytest.raises(FileTooLargeError):
            validate_upload(
                filename="big.txt", content_type="text/plain", data=b"x" * 100, max_size=50
            )


class TestExtractPages:
    def test_pdf_extracts_text_with_page_number(self) -> None:
        pages = extract_pages("doc.pdf", make_pdf("Annual leave is 20 days per year."))
        assert len(pages) == 1
        assert pages[0].page_number == 1
        assert "20 days" in pages[0].text

    def test_txt_extracts_single_page(self) -> None:
        pages = extract_pages("notes.txt", b"Hello world, this is plain text.")
        assert len(pages) == 1
        assert pages[0].page_number == 1
        assert "Hello world" in pages[0].text

    def test_markdown_extracts_single_page(self) -> None:
        pages = extract_pages("notes.md", b"# Title\n\nSome content here.")
        assert len(pages) == 1
        assert "Some content" in pages[0].text

    def test_blank_pdf_raises_ocr_hint(self) -> None:
        with pytest.raises(EmptyExtractionError, match="OCR"):
            extract_pages("blank.pdf", make_blank_pdf())

    def test_near_empty_text_raises(self) -> None:
        with pytest.raises(EmptyExtractionError):
            extract_pages("tiny.txt", b"hi")
