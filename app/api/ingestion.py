import os
import tempfile
from fastapi import APIRouter, File, HTTPException, UploadFile, status
from app.ingestion.pipeline import ingest_pdf

router = APIRouter(prefix="/api/v1", tags=["Ingestion"])


@router.post("/ingest")
async def ingest_document(file: UploadFile = File(...)):
    """
    Accepts an uploaded PDF file, saves it temporarily with .pdf extension,
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

        result = await ingest_pdf(file_path=temp_file_path, filename=file.filename)
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
