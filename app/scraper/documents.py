"""Text extraction from tender documents."""
import io
import logging
import re
import zipfile

log = logging.getLogger(__name__)


def extract_text(content: bytes, content_type: str = "", filename: str = "", ocr: bool = False) -> str | None:
    """Returns extracted text, '' if the file is readable but has no text, None if the format is unsupported."""
    name = (filename or "").lower()
    if content_type == "application/pdf" or name.endswith(".pdf") or content[:5] == b"%PDF-":
        return _pdf_text(content, ocr)
    if name.endswith(".docx") or "wordprocessingml" in content_type:
        return _docx_text(content)
    if content_type.startswith("text/") or name.endswith(".txt"):
        return content.decode("utf-8", "ignore").strip()
    return None


def _pdf_text(content: bytes, ocr: bool) -> str:
    import pymupdf
    parts = []
    with pymupdf.open(stream=content, filetype="pdf") as doc:
        for page in doc:
            text = page.get_text()
            if len(text.strip()) < 20 and ocr:
                try:  # needs the tesseract binary installed
                    text = page.get_textpage_ocr(full=True).extractText()
                except Exception as e:  # noqa: BLE001
                    log.warning("OCR unavailable: %s", e)
            parts.append(text)
    return re.sub(r"[ \t]+", " ", "\n".join(parts)).strip()


def _docx_text(content: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        xml = z.read("word/document.xml").decode("utf-8", "ignore")
    xml = re.sub(r"</w:p>", "\n", xml)
    return re.sub(r"\s*\n\s*", "\n", re.sub(r"<[^>]+>", "", xml)).strip()
