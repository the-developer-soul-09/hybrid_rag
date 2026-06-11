"""
Central configuration for the PDF RAG pipeline.
Loads environment variables and defines all tunable constants.
"""

import os
import sys
import logging
from pathlib import Path
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Load .env from project root
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(_PROJECT_ROOT / ".env")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("pdf_rag")

# ---------------------------------------------------------------------------
# Groq LLM
# ---------------------------------------------------------------------------
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"
GROQ_MODEL: str = "openai/gpt-oss-120b"
GROQ_MAX_TOKENS: int = 4096
GROQ_TEMPERATURE: float = 0.2

if not GROQ_API_KEY:
    logger.error(
        "GROQ_API_KEY is not set. "
        "Create a .env file in the project root with: GROQ_API_KEY=<your-key>"
    )
    sys.exit(1)

# ---------------------------------------------------------------------------
# Embedding models
# ---------------------------------------------------------------------------
DENSE_MODEL_NAME: str = "sentence-transformers/all-MiniLM-L6-v2"
DENSE_VECTOR_SIZE: int = 384

SPARSE_MODEL_NAME: str = "Qdrant/bm25"

# ---------------------------------------------------------------------------
# Qdrant vector store
# ---------------------------------------------------------------------------
QDRANT_PATH: str = str(_PROJECT_ROOT / "qdrant_data")
QDRANT_COLLECTION: str = "pdf_documents"

# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------
CHUNK_SIZE: int = 512          # target chars per chunk
CHUNK_OVERLAP: int = 64        # overlap in chars between consecutive chunks
MIN_CHUNK_SIZE: int = 50       # discard chunks shorter than this

# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------
RETRIEVAL_TOP_K: int = 10       # final results returned to LLM
PREFETCH_LIMIT: int = 50       # candidates fetched per retrieval leg

# ---------------------------------------------------------------------------
# OCR / PDF processing
# ---------------------------------------------------------------------------
TESSERACT_CMD: str = os.getenv("TESSERACT_CMD", "tesseract")
TESSERACT_LANG: str = "eng"
DPI: int = 300                  # resolution for pdf2image conversion
SCANNED_TEXT_THRESHOLD: int = 30  # min chars to consider a page "digital"

# ---------------------------------------------------------------------------
# Table Transformer models
# ---------------------------------------------------------------------------
TABLE_DETECTION_MODEL: str = "microsoft/table-transformer-detection"
TABLE_STRUCTURE_MODEL: str = (
    "microsoft/table-transformer-structure-recognition-v1.1-all"
)
TABLE_DETECTION_THRESHOLD: float = 0.7
TABLE_STRUCTURE_THRESHOLD: float = 0.6

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
UPLOAD_DIR: Path = _PROJECT_ROOT / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
