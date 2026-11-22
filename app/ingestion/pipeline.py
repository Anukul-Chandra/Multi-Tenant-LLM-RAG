import uuid
from typing import Any, Dict
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from llama_parse import LlamaParse
from qdrant_client.models import PointStruct

from app.core.config import settings
from app.core.qdrant import COLLECTION_NAME, client, ensure_collection_exists


async def ingest_pdf(file_path: str, filename: str) -> Dict[str, Any]:
    """
    Asynchronously parses a PDF using LlamaParse, splits text into chunks,
    generates 768-dim embeddings with Gemini, and upserts them into Qdrant.
    """
    if not settings.LLAMA_CLOUD_API_KEY:
        raise ValueError("LLAMA_CLOUD_API_KEY is missing. Please configure it in .env.")
    if not settings.GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY is missing. Please configure it in .env.")

    # 1. Parse PDF asynchronously with LlamaParse
    parser = LlamaParse(
        api_key=settings.LLAMA_CLOUD_API_KEY,
        result_type="markdown",
    )
    documents = await parser.aload_data(file_path)

    # 2. Split parsed markdown documents into chunks
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200,
    )
    combined_text = "\n\n".join([doc.text for doc in documents if getattr(doc, "text", None)])
    chunks = text_splitter.split_text(combined_text) if combined_text else []

    # 3. Generate embeddings for chunks
    if chunks:
        embeddings = GoogleGenerativeAIEmbeddings(
            model="gemini-embedding-001",
            google_api_key=settings.GEMINI_API_KEY,
            output_dimensionality=768,
        )
        vectors = await embeddings.aembed_documents(chunks)

        # 4. Ensure Qdrant collection exists and upsert PointStruct records
        ensure_collection_exists()

        points = [
            PointStruct(
                id=str(uuid.uuid4()),
                vector=vector,
                payload={
                    "text": chunk,
                    "source": filename,
                    "chunk_index": idx,
                },
            )
            for idx, (chunk, vector) in enumerate(zip(chunks, vectors))
        ]

        client.upsert(
            collection_name=COLLECTION_NAME,
            points=points,
        )
    else:
        ensure_collection_exists()

    return {
        "filename": filename,
        "chunks_indexed": len(chunks),
    }
