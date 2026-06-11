"""
Central configuration for the ChatDoc pipeline.
All values can be overridden via environment variables / .env file.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(_PROJECT_ROOT / ".env")

# Groq LLM
GROQ_API_KEY  : str   = os.getenv("GROQ_API_KEY", "")
GROQ_BASE_URL : str   = "https://api.groq.com/openai/v1"
GROQ_MODEL    : str   = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_MAX_TOKENS: int  = 4096
GROQ_TEMPERATURE: float = 0.2

# Document ingestion
MAX_DOC_CHARS : int   = 100_000   # hard char truncation before sending to LLM (~25k tokens)

# Paths
UPLOAD_DIR: Path = _PROJECT_ROOT / "uploads"
STATIC_DIR: Path = _PROJECT_ROOT / "static"
UPLOAD_DIR.mkdir(exist_ok=True)
