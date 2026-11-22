from typing import Any, Dict, List, TypedDict


class GraphState(TypedDict):
    question: str
    documents: List[Dict[str, Any]]
    generation: str
    sources: List[str]
    is_grounded: bool
