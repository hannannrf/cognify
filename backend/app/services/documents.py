import io
import re

from pypdf import PdfReader
from pypdf.errors import PdfReadError

CHUNK_CHARS = 1000
OVERLAP_CHARS = 200


class BadDocument(ValueError):
    pass


def extract_pages(data: bytes) -> list[str]:
    """Return cleaned text per page (index 0 = page 1)."""
    try:
        reader = PdfReader(io.BytesIO(data))
        return [re.sub(r"\s+", " ", page.extract_text() or "").strip() for page in reader.pages]
    except (PdfReadError, ValueError, KeyError) as e:
        raise BadDocument(f"Could not read PDF: {e}") from e


def chunk_pages(pages: list[str]) -> list[tuple[int, str]]:
    """Split each page into overlapping windows; chunks never cross pages so citations stay exact."""
    chunks = []
    step = CHUNK_CHARS - OVERLAP_CHARS
    for page_number, text in enumerate(pages, start=1):
        for start in range(0, max(len(text) - OVERLAP_CHARS, 1), step):
            piece = text[start : start + CHUNK_CHARS].strip()
            if piece:
                chunks.append((page_number, piece))
    return chunks
