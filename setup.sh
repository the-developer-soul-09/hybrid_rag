#!/usr/bin/env bash
# =============================================================================
# Setup script for PDF RAG Pipeline
# Installs system dependencies and Python packages
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/20lpavenv"

echo "============================================"
echo "  PDF RAG Pipeline — Setup"
echo "============================================"

# ---- System dependencies ---------------------------------------------------
echo ""
echo "[1/4] Installing system dependencies..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    tesseract-ocr \
    tesseract-ocr-eng \
    poppler-utils \
    libgl1-mesa-glx \
    libglib2.0-0 \
    2>&1 | tail -5

echo "  ✓ Tesseract: $(tesseract --version 2>&1 | head -1)"
echo "  ✓ Poppler:   $(pdftoppm -v 2>&1 | head -1)"

# ---- Virtual environment ---------------------------------------------------
echo ""
echo "[2/4] Setting up Python virtual environment..."
if [ ! -d "${VENV_DIR}" ]; then
    python3 -m venv "${VENV_DIR}"
fi
source "${VENV_DIR}/bin/activate"
pip install --upgrade pip -q

# ---- Python packages -------------------------------------------------------
echo ""
echo "[3/4] Installing Python dependencies..."
pip install -r "${SCRIPT_DIR}/requirements.txt" -q

# ---- Verify ----------------------------------------------------------------
echo ""
echo "[4/4] Verifying installation..."
python3 -c "
import pdfplumber; print(f'  ✓ pdfplumber {pdfplumber.__version__}')
import pytesseract; print(f'  ✓ pytesseract OK')
import torch; print(f'  ✓ PyTorch {torch.__version__}')
from sentence_transformers import SentenceTransformer; print(f'  ✓ sentence-transformers OK')
from qdrant_client import QdrantClient; print(f'  ✓ qdrant-client OK')
import openai; print(f'  ✓ openai OK')
from fastembed import SparseTextEmbedding; print(f'  ✓ fastembed OK')
"

echo ""
echo "============================================"
echo "  Setup complete! Usage:"
echo "    source ${VENV_DIR}/bin/activate"
echo "    python main.py ingest <your.pdf>"
echo "    python main.py query 'your question'"
echo "============================================"
