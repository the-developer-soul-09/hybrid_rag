import os
import shutil
import logging
from typing import Optional
from pydantic import BaseModel
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

# Initialize logging
logger = logging.getLogger("pdf_rag_server")
logging.basicConfig(level=logging.INFO)

# Create FastAPI app
app = FastAPI(
    title="PDF Hybrid RAG Pipeline API",
    description="Backend service for ingestion and hybrid retrieval QA.",
    version="1.0.0"
)

# Enable CORS for frontend compatibility
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure upload directory exists
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Define static directories
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(STATIC_DIR, exist_ok=True)


# Models
class Message(BaseModel):
    role: str
    content: str


class QueryRequest(BaseModel):
    question: str
    history: Optional[list[Message]] = None


# Global instances for RAG models (lazy loaded on first query)
_rag_instances = {}

def get_rag_components():
    if not _rag_instances:
        from rag.embeddings import DenseEmbedder, SparseEmbedder
        from rag.retriever import HybridRetriever
        from rag.generator import GroqGenerator
        from rag.vector_store import QdrantStore

        logger.info("Initializing models and retriever components...")
        dense_embedder = DenseEmbedder()
        sparse_embedder = SparseEmbedder()
        _rag_instances["retriever"] = HybridRetriever(dense_embedder, sparse_embedder)
        _rag_instances["generator"] = GroqGenerator()
        _rag_instances["store"] = QdrantStore()
        logger.info("RAG components loaded successfully.")
    return _rag_instances


# Endpoints
@app.get("/")
def read_root():
    """Serve the index.html chatbot page."""
    index_path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.exists(index_path):
        raise HTTPException(
            status_code=404, 
            detail="index.html not found in static/ directory. Please ensure the frontend code is generated."
        )
    return FileResponse(index_path)


@app.post("/upload")
async def upload_pdf(file: UploadFile = File(...), reset: bool = Form(False)):
    """
    Upload a PDF, save it to uploads/, and run the RAG ingestion pipeline.
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    file_path = os.path.join(UPLOAD_DIR, file.filename)
    try:
        # Save file to disk
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # Call the ingestion logic from main.py
        from main import ingest_pdf
        logger.info(f"Starting ingestion for {file.filename} (reset={reset})...")
        
        # Run ingestion
        ingest_pdf(file_path, reset=reset)
        
        return {
            "success": True, 
            "message": f"File '{file.filename}' uploaded and ingested successfully.",
            "filename": file.filename
        }
    except Exception as e:
        logger.error(f"Failed to ingest file: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {str(e)}")


@app.post("/query")
async def query_rag(request: QueryRequest):
    """
    Execute hybrid search query and generate answers using the LLM.
    """
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    try:
        components = get_rag_components()
        retriever = components["retriever"]
        generator = components["generator"]

        # Parse history
        history_dicts = []
        if request.history:
            history_dicts = [{"role": msg.role, "content": msg.content} for msg in request.history]

        # Rephrase the question using history if available to perform a standalone search
        search_query = request.question
        if history_dicts:
            try:
                search_query = generator.rephrase_query(request.question, history_dicts)
                logger.info(f"Rephrased user query: '{request.question}' -> Standalone search: '{search_query}'")
            except Exception as e:
                logger.warning(f"Failed to rephrase query: {e}. Using original question.")

        # Retrieve relevant chunks using the search query
        chunks = retriever.retrieve(search_query)
        if not chunks:
            return {
                "answer": "No relevant content found in the database. Please make sure you have uploaded and ingested some PDFs first.",
                "sources": []
            }

        # Generate response using both chunks and conversation history
        answer = generator.generate(request.question, chunks, history=history_dicts)

        # Build clean source outputs
        sources = [
            {
                "page_num": chunk.page_num,
                "chunk_type": chunk.chunk_type,
                "score": float(chunk.score),
                "source_doc": chunk.source_doc,
                "text": chunk.text
            }
            for chunk in chunks
        ]

        return {
            "success": True,
            "answer": answer,
            "sources": sources
        }
    except Exception as e:
        logger.error(f"Query processing failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Query execution failed: {str(e)}")


@app.get("/info")
def get_info():
    """Retrieve collection status and chunk count."""
    try:
        components = get_rag_components()
        store = components["store"]
        info = store.get_collection_info()
        return {"success": True, "info": info}
    except Exception as e:
        logger.error(f"Failed to retrieve stats: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/reset")
def reset_database():
    """Clear all vector collection points."""
    try:
        components = get_rag_components()
        store = components["store"]
        store.reset_collection()
        return {"success": True, "message": "Database collection has been cleared."}
    except Exception as e:
        logger.error(f"Failed to reset database: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)
