import logging
import uuid
from typing import Any, Dict
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from llama_parse import LlamaParse
from pypdf import PdfReader
from qdrant_client.models import PointStruct

from app.core.config import settings
from app.core.qdrant import COLLECTION_NAME, client, ensure_collection_exists

logger = logging.getLogger(__name__)


async def ingest_pdf(file_path: str, filename: str) -> Dict[str, Any]:
    """
    Asynchronously parses a PDF using LlamaParse (with pypdf fallback),
    splits text into chunks, generates 768-dim embeddings with Gemini,
    and upserts them into Qdrant.
    """
    if not settings.GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY is missing. Please configure it in .env.")

    combined_text = ""

    # 1. Attempt parsing with LlamaParse
    if settings.LLAMA_CLOUD_API_KEY:
        try:
            parser = LlamaParse(
                api_key=settings.LLAMA_CLOUD_API_KEY,
                result_type="markdown",
                verbose=True,
            )
            documents = await parser.aload_data(file_path, extra_info={"file_name": filename})
            doc_count = len(documents) if documents else 0
            print(f"[Ingestion] LlamaParse returned {doc_count} documents for '{filename}'.")
            logger.info(f"LlamaParse returned {doc_count} documents for '{filename}'.")

            if doc_count > 0:
                combined_text = "\n\n".join([doc.text for doc in documents if getattr(doc, "text", None)])

            if not combined_text.strip():
                warning_msg = f"LlamaParse failed/empty, falling back to pypdf for '{filename}'."
                print(f"[Ingestion] Warning: {warning_msg}")
                logger.warning(warning_msg)
        except Exception as exc:
            warning_msg = f"LlamaParse failed/empty, falling back to pypdf. Error: {exc}"
            print(f"[Ingestion] Warning: {warning_msg}")
            logger.warning(warning_msg)
    else:
        warning_msg = f"LlamaParse failed/empty, falling back to pypdf (LLAMA_CLOUD_API_KEY not configured)."
        print(f"[Ingestion] Warning: {warning_msg}")
        logger.warning(warning_msg)

    # 2. Fallback to pypdf page-by-page extraction if LlamaParse produced no text
    if not combined_text.strip():
        try:
            reader = PdfReader(file_path)
            page_texts = []
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    page_texts.append(text)
            combined_text = "\n\n".join(page_texts)
            print(f"[Ingestion] pypdf fallback extracted text from {len(page_texts)} pages.")
            logger.info(f"pypdf fallback extracted text from {len(page_texts)} pages.")
        except Exception as exc:
            raise ValueError(f"Both LlamaParse and pypdf fallback failed to extract text: {exc}")

    if not combined_text.strip():
        raise ValueError("No text could be extracted from the PDF using LlamaParse or pypdf fallback.")

    # 3. Split extracted text into chunks
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200,
    )
    chunks = text_splitter.split_text(combined_text)

    if not chunks:
        raise ValueError("Extracted text could not be split into chunks.")

    # 4. Generate embeddings for chunks
    embeddings = GoogleGenerativeAIEmbeddings(
        model="gemini-embedding-001",
        google_api_key=settings.GEMINI_API_KEY,
        output_dimensionality=768,
    )
    vectors = await embeddings.aembed_documents(chunks)

    # 5. Ensure Qdrant collection exists and upsert PointStruct records
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

    return {
        "filename": filename,
        "chunks_indexed": len(chunks),
    }
