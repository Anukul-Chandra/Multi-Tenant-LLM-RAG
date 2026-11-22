from fastapi import FastAPI
from app.core.config import settings

app = FastAPI(
    title="Swiss LMS RAG",
    description="Production-grade RAG backend for Swiss Education LMS",
    version="0.1.0",
)


@app.get("/health")
async def health_check():
    return {"status": "ok", "project": "Swiss LMS RAG"}
