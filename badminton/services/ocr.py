"""Reading the text in a booking screenshot (FR-BK-12) with Tesseract, a traditional
OCR engine: no LLM, and no network call.

The tesserocr wheel bundles Tesseract itself, so nothing needs installing on the
server. Its English model ships in ``badminton/tessdata``.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

TESSDATA = Path(__file__).resolve().parents[1] / "tessdata"

logger = logging.getLogger(__name__)


def read_text(image: bytes) -> str:
    """The text in an image (JPEG, PNG or WebP), or ``""`` if it can't be read.
    It takes about half a second and blocks, so run it in a thread."""
    try:
        import tesserocr  # here, not at the top: loading Tesseract takes ~0.1 s

        with tempfile.NamedTemporaryFile() as file:  # deleted as soon as it's read
            file.write(image)
            file.flush()
            with tesserocr.PyTessBaseAPI(path=str(TESSDATA), lang="eng") as api:
                api.SetImageFile(file.name)
                return api.GetUTF8Text()
    # Any failure means there's nothing to read, and the member types the details in:
    # Tesseract not loading, the temp file, or the image. A file Tesseract can't decode
    # even raises NameError, because tesserocr falls back to Pillow, which isn't installed.
    except Exception:
        logger.warning("Could not read the screenshot", exc_info=True)
        return ""
