from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from app.rag.graph import rag_app

router = APIRouter(prefix="/api/v1", tags=["Chat"])


class ChatRequest(BaseModel):
    question: str


@router.post("/chat")
async def chat(request: ChatRequest):
    """
    Executes the CRAG + Self-RAG pipeline on the user's question,
    returning a grounded answer with relevant sources.
    """
    if not request.question or not request.question.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Question cannot be empty.",
        )

    try:
        initial_state = {
            "question": request.question.strip(),
            "documents": [],
            "generation": "",
            "sources": [],
            "is_grounded": False,
        }
        final_state = await rag_app.ainvoke(initial_state)
        return {
            "answer": final_state.get("generation", ""),
            "sources": final_state.get("sources", []),
            "grounded": final_state.get("is_grounded", False),
        }
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"RAG processing failed: {str(exc)}",
        )
