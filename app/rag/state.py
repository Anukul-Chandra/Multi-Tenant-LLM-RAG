from typing import Any, Dict, List, TypedDict


class GraphState(TypedDict):
    question: str
    chat_history: List[Dict[str, str]]
    rewritten_question: str
    documents: List[Dict[str, Any]]
    page_context: str
    generation: str
    sources: List[str]
    is_grounded: bool
    project_id: str
