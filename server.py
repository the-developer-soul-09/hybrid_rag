import os
import shutil
import logging
from typing import Optional

from pydantic import BaseModel
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("chatdoc")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
GROQ_API_KEY  = os.getenv("GROQ_API_KEY", "")
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_MODEL    = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
MAX_TOKENS    = 4096
MAX_DOC_CHARS = 100_000   # ~25k tokens — hard truncation before sending to LLM

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Groq client
# ---------------------------------------------------------------------------
if not GROQ_API_KEY:
    logger.error("GROQ_API_KEY is not set. Add it to your .env file.")

groq_client = OpenAI(api_key=GROQ_API_KEY, base_url=GROQ_BASE_URL) if GROQ_API_KEY else None

# ---------------------------------------------------------------------------
# In-memory document store  { session_id -> {text, filename, char_count, truncated} }
# ---------------------------------------------------------------------------
_documents: dict[str, dict] = {}

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="ChatDoc API",
    description="Upload a PDF and chat with its contents.",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class Message(BaseModel):
    role: str
    content: str


class QueryRequest(BaseModel):
    question: str
    history: Optional[list[Message]] = None
    session_id: Optional[str] = "default"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/")
def read_root():
    """Serve the frontend chatbot page."""
    index_path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.exists(index_path):
        raise HTTPException(status_code=404, detail="index.html not found in static/")
    return FileResponse(index_path)


@app.post("/upload")
async def upload_pdf(
    file: UploadFile = File(...),
    session_id: str = Form("default"),
    reset: bool = Form(False),
):
    """
    Upload a PDF, extract its text, truncate to MAX_DOC_CHARS, and store in memory.
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    file_path = os.path.join(UPLOAD_DIR, file.filename)
    try:
        with open(file_path, "wb") as buf:
            shutil.copyfileobj(file.file, buf)

        from pdf_processor.digital_extractor import extract_pdf_text
        doc_text, truncated = extract_pdf_text(file_path, max_chars=MAX_DOC_CHARS)

        _documents[session_id] = {
            "text": doc_text,
            "filename": file.filename,
            "char_count": len(doc_text),
            "truncated": truncated,
        }

        logger.info("Loaded '%s' into session '%s' (%d chars)", file.filename, session_id, len(doc_text))

        return {
            "success": True,
            "message": f"'{file.filename}' loaded successfully.",
            "filename": file.filename,
            "char_count": len(doc_text),
            "truncated": truncated,
        }

    except Exception as e:
        logger.error("Upload failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@app.post("/query")
async def query(request: QueryRequest):
    """
    Answer a question about the loaded document using the full document text as context.
    """
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    if not groq_client:
        raise HTTPException(status_code=500, detail="GROQ_API_KEY is not configured.")

    session_id = request.session_id or "default"
    doc = _documents.get(session_id)
    if not doc:
        raise HTTPException(
            status_code=400,
            detail="No document loaded. Please upload a PDF first.",
        )

    # Build system prompt with full document text embedded
    system_prompt = (
        f"You are a helpful assistant that answers questions about the document below.\n"
        f"Answer strictly based on the document content. "
        f"If the answer is not in the document, say so clearly. "
        f"Cite page numbers (e.g. [Page 3]) when referencing specific content.\n\n"
        f"--- DOCUMENT: {doc['filename']} ---\n"
        f"{doc['text']}\n"
        f"--- END OF DOCUMENT ---"
    )

    messages: list[dict] = [{"role": "system", "content": system_prompt}]

    # Append conversation history
    if request.history:
        for msg in request.history:
            messages.append({"role": msg.role, "content": msg.content})

    messages.append({"role": "user", "content": request.question})

    try:
        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            max_tokens=MAX_TOKENS,
            temperature=0.2,
        )
        answer = response.choices[0].message.content or ""
        return {"success": True, "answer": answer, "sources": []}

    except Exception as e:
        logger.error("Query failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Query failed: {str(e)}")


@app.get("/info")
def get_info(session_id: str = "default"):
    """Return info about the currently loaded document."""
    doc = _documents.get(session_id)
    if not doc:
        return {"success": True, "info": {"status": "No document loaded"}}
    return {
        "success": True,
        "info": {
            "filename": doc["filename"],
            "char_count": doc["char_count"],
            "truncated": doc["truncated"],
            "status": "loaded",
        },
    }


@app.post("/reset")
def reset_session(session_id: str = "default"):
    """Clear the loaded document for a session."""
    _documents.pop(session_id, None)
    return {"success": True, "message": "Document cleared."}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)
