from fastapi import FastAPI, HTTPException, status
from app.api.ingestion import router as ingestion_router
from app.core import qdrant

app = FastAPI(
    title="Swiss LMS RAG",
    description="Production-grade RAG backend for Swiss Education LMS",
    version="0.1.0",
)

app.include_router(ingestion_router)


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
