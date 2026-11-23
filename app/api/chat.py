from typing import Any, Dict, List
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from app.rag.graph import rag_app

router = APIRouter(prefix="/api/v1", tags=["Chat"])

# Lightweight in-memory session store: session_id -> list of messages
SESSION_STORE: Dict[str, List[Dict[str, str]]] = {}


class ChatRequest(BaseModel):
    question: str
    session_id: str = "default"
    project_id: str = "default"


@router.post("/chat")
async def chat(request: ChatRequest):
    """
    Executes the CRAG + Self-RAG pipeline with conversational memory,
    query rewriting, and multi-tenant project_id isolation.
    """
    if not request.question or not request.question.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Question cannot be empty.",
        )

    try:
        session_id = request.session_id.strip() if request.session_id else "default"
        project_id = request.project_id.strip() if request.project_id else "default"
        chat_history = list(SESSION_STORE.get(session_id, []))

        initial_state = {
            "question": request.question.strip(),
            "chat_history": chat_history,
            "rewritten_question": "",
            "documents": [],
            "generation": "",
            "sources": [],
            "is_grounded": False,
            "project_id": project_id,
        }

        final_state = await rag_app.ainvoke(initial_state)

        answer = final_state.get("generation", "")
        rewritten_question = final_state.get("rewritten_question", request.question.strip())
        sources = final_state.get("sources", [])
        is_grounded = final_state.get("is_grounded", False)

        # Update in-memory session history (keep last 6 messages)
        history = SESSION_STORE.setdefault(session_id, [])
        history.append({"role": "user", "content": request.question.strip()})
        history.append({"role": "assistant", "content": answer})
        SESSION_STORE[session_id] = history[-6:]

        return {
            "session_id": session_id,
            "rewritten_question": rewritten_question,
            "answer": answer,
            "sources": sources,
            "grounded": is_grounded,
        }
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"RAG processing failed: {str(exc)}",
        )
