import re
from typing import Dict, List, Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from app.rag.graph import rag_app

router = APIRouter(prefix="/api/v1", tags=["Chat"])

# Lightweight in-memory session store: session_id -> list of messages
SESSION_STORE: Dict[str, List[Dict[str, str]]] = {}

# Set of common greetings and small talk phrases
GREETINGS_SET = {
    "hi",
    "hey",
    "hello",
    "yo",
    "hola",
    "assalamu alaikum",
    "assalamualaikum",
    "good morning",
    "good afternoon",
    "good evening",
    "how are you",
    "how are you doing",
    "thanks",
    "thank you",
    "thank you so much",
    "thanks a lot",
    "thx",
    "bye",
    "goodbye",
}


class ChatRequest(BaseModel):
    question: str
    session_id: str = "default"
    project_id: str = "default"
    page_context: Optional[str] = ""


@router.post("/chat")
async def chat(request: ChatRequest):
    """
    Executes conversational chat. Intercepts common greetings and small talk
    immediately for zero-latency friendly responses, otherwise delegates to
    the LangGraph CRAG + Self-RAG pipeline.
    """
    if not request.question or not request.question.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Question cannot be empty.",
        )

    session_id = request.session_id.strip() if request.session_id else "default"
    project_id = request.project_id.strip() if request.project_id else "default"

    # 1. Immediate Greeting / Small Talk Interception (Before LangGraph / LLM)
    normalized_q = re.sub(r"[^a-zA-Z0-9\s]", "", request.question.strip().lower())
    clean_q = re.sub(r"\s+", " ", normalized_q).strip()

    if clean_q in GREETINGS_SET:
        if clean_q in {"thanks", "thank you", "thank you so much", "thanks a lot", "thx"}:
            greeting_answer = "You're very welcome! Feel free to ask if you have any questions about the documents or project."
        elif clean_q in {"bye", "goodbye"}:
            greeting_answer = "Goodbye! Have a great day, and feel free to return anytime you need help. 👋"
        elif clean_q in {"how are you", "how are you doing"}:
            greeting_answer = "I'm doing great, thank you for asking! 😊 I'm ready to help you with any questions about the documents or project."
        else:
            greeting_answer = "Hey there! 👋 I'm the AI assistant for this project. Ask me anything about the uploaded documents, skills, or experience!"

        # Update in-memory session history
        history = SESSION_STORE.setdefault(session_id, [])
        history.append({"role": "user", "content": request.question.strip()})
        history.append({"role": "assistant", "content": greeting_answer})
        SESSION_STORE[session_id] = history[-6:]

        return {
            "session_id": session_id,
            "rewritten_question": request.question.strip(),
            "answer": greeting_answer,
            "sources": [],
            "grounded": True,
        }

    # 2. Main LangGraph CRAG + Self-RAG Pipeline
    try:
        chat_history = list(SESSION_STORE.get(session_id, []))
        page_context = (request.page_context or "").strip()[:4500]

        initial_state = {
            "question": request.question.strip(),
            "chat_history": chat_history,
            "rewritten_question": "",
            "documents": [],
            "page_context": page_context,
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
