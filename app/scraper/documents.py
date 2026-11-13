"""Text extraction from tender documents using PaddleOCR and Tesseract fallback."""

import io
import logging
import re
import zipfile

log = logging.getLogger(__name__)

_PADDLE_ENGINE = None


def extract_text(
    content: bytes,
    content_type: str = "",
    filename: str = "",
    ocr: bool = False,
) -> str | None:
    """Extract text from supported tender documents."""

    name = (filename or "").lower()

    if (
        content_type == "application/pdf"
        or name.endswith(".pdf")
        or content[:5] == b"%PDF-"
    ):
        return _pdf_text(content, ocr)

    if name.endswith(".docx") or "wordprocessingml" in content_type:
        return _docx_text(content)

    if content_type.startswith("text/") or name.endswith(".txt"):
        return content.decode("utf-8", "ignore").strip()

    return None


def _paddle_ocr(image) -> str:
    """Extract text using PaddleOCR."""

    import numpy as np
    from paddleocr import PaddleOCR

    global _PADDLE_ENGINE

    if _PADDLE_ENGINE is None:
        _PADDLE_ENGINE = PaddleOCR(
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )

    result = _PADDLE_ENGINE.predict(input=np.array(image))
    parts = []

    for item in result:
        data = item.json
        parts.extend(data["res"].get("rec_texts", []))

    output = "\n".join(parts).strip()
    log.info("PaddleOCR returned %s result items and %s characters",
             len(result), len(output))

    return output


def _tesseract_ocr(image) -> str:
    """Fallback OCR using Tesseract."""

    import pytesseract

    return pytesseract.image_to_string(
        image,
        lang="eng",
        config="--oem 3 --psm 6",
    ).strip()


def _quality_score(text: str) -> float:
    """Estimate useful OCR text quality."""

    if not text:
        return 0.0

    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0

    useful = sum(c.isalnum() for c in chars)
    return useful / len(chars)


def _pdf_text(content: bytes, ocr: bool) -> str:
    import pymupdf
    from PIL import Image, ImageEnhance

    parts = []

    with pymupdf.open(stream=content, filetype="pdf") as doc:
        for page_number, page in enumerate(doc, start=1):
            embedded = page.get_text().strip()
            text = embedded

            # OCR scanned pages or pages with poor embedded text.
            should_ocr = (
                ocr
                and (
                    len(embedded) < 20
                    or (
                        len(embedded) >= 20
                        and _quality_score(embedded) < 0.65
                    )
                )
            )

            if should_ocr:
                try:
                    pix = page.get_pixmap(
                        matrix=pymupdf.Matrix(200 / 72, 200 / 72),
                        colorspace=pymupdf.csRGB,
                        alpha=False,
                    )

                    image = Image.open(
                        io.BytesIO(pix.tobytes("png"))
                    ).convert("RGB")

                    paddle_text = _paddle_ocr(image)

                    if (
                        paddle_text
                        and len(paddle_text.strip()) > len(text.strip())
                        and _quality_score(paddle_text) >= 0.5
                    ):
                        text = paddle_text

                    # Try Tesseract if PaddleOCR output is weak.
                    if _quality_score(text) < 0.65 or len(text) < 20:
                        gray = image.convert("L")
                        gray = ImageEnhance.Contrast(gray).enhance(2.0)
                        fallback = _tesseract_ocr(gray)

                        if _quality_score(fallback) > _quality_score(text):
                            text = fallback

                    log.info(
                        "PDF page %s: embedded=%s, selected=%s characters",
                        page_number,
                        len(embedded),
                        len(text),
                    )

                except Exception as e:
                    log.exception(
                        "OCR failed on PDF page %s: %s",
                        page_number,
                        e,
                    )

                    if not text:
                        try:
                            text = page.get_textpage_ocr(
                                language="eng",
                                dpi=300,
                                full=True,
                            ).extractText()
                        except Exception as fallback_error:
                            log.warning(
                                "PyMuPDF OCR fallback failed: %s",
                                fallback_error,
                            )

            parts.append(text)

    return re.sub(r"[ \t]+", " ", "\n".join(parts)).strip()


def _docx_text(content: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        xml = z.read("word/document.xml").decode("utf-8", "ignore")

    xml = re.sub(r"</w:p>", "\n", xml)

    return re.sub(
        r"\s*\n\s*",
        "\n",
        re.sub(r"<[^>]+>", "", xml),
    ).strip()
