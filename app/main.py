import os
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.api.chat import router as chat_router
from app.api.ingestion import router as ingestion_router
from app.core import qdrant

app = FastAPI(
    title="Swiss LMS RAG",
    description="Production-grade RAG backend for Swiss Education LMS",
    version="0.1.0",
)

# CORS Middleware allowing external clients (e.g. portfolio, LMS frontend)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ingestion_router)
app.include_router(chat_router)


@app.get("/")
async def root():
    """Serves the lightweight demo single-page application."""
    html_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "index.html")
    if not os.path.exists(html_path):
        html_path = "static/index.html"
    return FileResponse(html_path)


@app.get("/health")
async def health_check():
    return {"status": "ok", "project": "Swiss LMS RAG"}


@app.get("/health/qdrant")
async def health_qdrant():
    try:
        qdrant.ensure_collection_exists()
        _ = qdrant.client.get_collections()
        return {
            "status": "connected",
            "collection": qdrant.COLLECTION_NAME,
        }
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Qdrant connection error: {str(exc)}",
        )
