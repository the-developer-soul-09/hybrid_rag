"""
OCR-based extractor for scanned PDF pages.

Converts PDF pages to images and applies Tesseract OCR with
image pre-processing for improved accuracy.
"""

from __future__ import annotations

import logging

import cv2
import numpy as np
import pytesseract
from PIL import Image

from config import TESSERACT_CMD, TESSERACT_LANG
from pdf_processor.classifier import PageContent

logger = logging.getLogger(__name__)

# Configure Tesseract binary path if set
if TESSERACT_CMD != "tesseract":
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD


def preprocess_image(image: Image.Image) -> Image.Image:
    """
    Apply image pre-processing to improve OCR accuracy.

    Steps:
      1. Convert to grayscale
      2. Apply adaptive thresholding for binarization
      3. Denoise with morphological operations

    Parameters
    ----------
    image : PIL.Image.Image
        The raw page image.

    Returns
    -------
    PIL.Image.Image
        Pre-processed image optimized for OCR.
    """
    # Convert PIL -> OpenCV (numpy)
    img_array = np.array(image)

    # Convert to grayscale if needed
    if len(img_array.shape) == 3:
        gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
    else:
        gray = img_array

    # Adaptive threshold for handling varied lighting in scans
    binary = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blockSize=15,
        C=8,
    )

    # Denoise — gentle morphological opening
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 1))
    cleaned = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel, iterations=1)

    return Image.fromarray(cleaned)


def extract_scanned_page(
    page_image: Image.Image,
    page_num: int,
    preprocess: bool = True,
) -> PageContent:
    """
    Extract text from a scanned page image using Tesseract OCR.

    Parameters
    ----------
    page_image : PIL.Image.Image
        The page rendered as an image (from pdf2image).
    page_num : int
        1-based page number.
    preprocess : bool
        Whether to apply image pre-processing before OCR.

    Returns
    -------
    PageContent
        Structured content with OCR'd text. Tables from scanned pages
        are handled separately by the table_extractor module.
    """
    if preprocess:
        processed = preprocess_image(page_image)
    else:
        processed = page_image

    try:
        # PSM 3 = Fully automatic page segmentation (default)
        ocr_config = f"--psm 3 -l {TESSERACT_LANG}"
        text = pytesseract.image_to_string(processed, config=ocr_config)
    except pytesseract.TesseractError:
        logger.error(
            "Page %d: Tesseract OCR failed", page_num, exc_info=True
        )
        text = ""

    # Clean up OCR artifacts
    text = _clean_ocr_text(text)

    logger.info(
        "Page %d [scanned/OCR]: %d chars extracted",
        page_num,
        len(text),
    )

    return PageContent(
        page_num=page_num,
        page_type="scanned",
        text=text,
        tables=[],  # Tables handled by table_extractor
    )


def ocr_image_region(
    image: Image.Image,
    bbox: tuple[int, int, int, int] | None = None,
) -> str:
    """
    OCR a specific region of an image (used for table cell extraction).

    Parameters
    ----------
    image : PIL.Image.Image
        The source image.
    bbox : tuple[int, int, int, int] | None
        Bounding box (x0, y0, x1, y1) to crop. If None, OCR the full image.

    Returns
    -------
    str
        Extracted text from the region.
    """
    if bbox is not None:
        region = image.crop(bbox)
    else:
        region = image

    # Use PSM 7 (single line) for small cell regions, PSM 6 (block) for larger
    w, h = region.size
    psm = 7 if h < 50 else 6

    try:
        text = pytesseract.image_to_string(
            region,
            config=f"--psm {psm} -l {TESSERACT_LANG}",
        )
        return text.strip()
    except pytesseract.TesseractError:
        logger.debug("OCR failed for region %s", bbox)
        return ""


def _clean_ocr_text(text: str) -> str:
    """Remove common OCR artifacts and normalize whitespace."""
    import re

    # Remove isolated single characters that are likely noise
    # (but keep single-letter words like 'a', 'I')
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        # Skip lines that are just noise characters
        if stripped and len(stripped) <= 2 and not stripped.isalnum():
            continue
        cleaned_lines.append(line)

    text = "\n".join(cleaned_lines)

    # Collapse excessive blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()
