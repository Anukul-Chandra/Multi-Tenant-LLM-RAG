import os
import tempfile
from typing import Any, Dict
from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from qdrant_client import models

from app.core.qdrant import COLLECTION_NAME, client, ensure_collection_exists
from app.ingestion.pipeline import ingest_pdf

router = APIRouter(prefix="/api/v1", tags=["Ingestion"])


@router.post("/ingest")
async def ingest_document(
    file: UploadFile = File(...),
    project_id: str = Form("default"),
):
    """
    Accepts an uploaded PDF file and project_id, saves it temporarily with .pdf extension,
    flushes and closes it, runs ingestion pipeline, and cleans up temp files in finally.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file format. Only PDF files (.pdf) are supported.",
        )

    temp_file_path = None
    try:
        content = await file.read()
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        temp_file_path = tmp.name
        tmp.write(content)
        tmp.flush()
        tmp.close()

        result = await ingest_pdf(
            file_path=temp_file_path,
            filename=file.filename,
            project_id=project_id.strip() if project_id else "default",
        )
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ingestion failed: {str(exc)}",
        )
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except OSError:
                pass
        await file.close()


@router.get("/documents")
async def list_documents(project_id: str = "default") -> Dict[str, Any]:
    """
    Scrolls Qdrant points filtered by project_id and returns unique source filenames.
    """
    try:
        ensure_collection_exists()
        target_project = project_id.strip() if project_id else "default"
        scroll_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="project_id",
                    match=models.MatchValue(value=target_project),
                )
            ]
        )
        unique_sources = set()
        offset = None

        while True:
            points, next_page_offset = client.scroll(
                collection_name=COLLECTION_NAME,
                scroll_filter=scroll_filter,
                limit=100,
                offset=offset,
                with_payload=True,
            )
            for pt in points:
                if pt.payload and "source" in pt.payload:
                    unique_sources.add(pt.payload["source"])

            if next_page_offset is None or not points:
                break
            offset = next_page_offset

        return {
            "project_id": target_project,
            "documents": sorted(list(unique_sources)),
        }
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list documents: {str(exc)}",
        )


@router.delete("/documents/{filename}")
async def delete_document(filename: str, project_id: str = "default") -> Dict[str, Any]:
    """
    Deletes all points in Qdrant matching BOTH project_id and source == filename.
    """
    try:
        ensure_collection_exists()
        target_project = project_id.strip() if project_id else "default"
        del_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="project_id",
                    match=models.MatchValue(value=target_project),
                ),
                models.FieldCondition(
                    key="source",
                    match=models.MatchValue(value=filename),
                ),
            ]
        )
        client.delete(
            collection_name=COLLECTION_NAME,
            points_selector=models.FilterSelector(filter=del_filter),
        )
        return {
            "status": "deleted",
            "filename": filename,
            "project_id": target_project,
        }
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete document: {str(exc)}",
        )
