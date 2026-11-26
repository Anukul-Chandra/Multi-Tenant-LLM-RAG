import asyncio
import logging
import re
from typing import Any, Dict, List
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from langchain_groq import ChatGroq
from langgraph.graph import END, START, StateGraph
from qdrant_client import models

from app.core.config import settings
from app.core.qdrant import COLLECTION_NAME, client
from app.rag.state import GraphState

logger = logging.getLogger(__name__)

FALLBACK_ANSWER = "I do not have that information in our official documents."

# Fast grading & verification LLM
groq_llm = ChatGroq(
    model=settings.GROQ_MODEL,
    temperature=0,
    api_key=settings.GROQ_API_KEY,
    timeout=20,
    max_retries=1,
)

# High-accuracy answer generation LLM
gemini_llm = ChatGoogleGenerativeAI(
    model=settings.GEMINI_MODEL,
    temperature=0,
    google_api_key=settings.GEMINI_API_KEY,
    timeout=20,
    max_retries=1,
)

# Query embedding model
embeddings = GoogleGenerativeAIEmbeddings(
    model="gemini-embedding-001",
    google_api_key=settings.GEMINI_API_KEY,
    output_dimensionality=768,
)

# Small-talk regex patterns for zero-latency instant routing
GREETINGS_PATTERN = r"^(hi|hello|hey|hola|hey\s*there|hi\s*there|hello\s*there|assalamu\s*alaikum|assalamualaikum|good\s*(morning|afternoon|evening|day)|greetings|howdy|yo|sup)$"
HOW_ARE_YOU_PATTERN = r"^(how\s*are\s*you(\s*doing)?|hows\s*it\s*going|how\s*is\s*it\s*going|whats\s*up)$"
THANKS_PATTERN = r"^(thanks|thank\s*you|thank\s*you\s*so\s*much|thanks\s*a\s*lot|thx|many\s*thanks)$"
IDENTITY_PATTERN = r"^(who\s*are\s*you|what\s*are\s*you|what\s*can\s*you\s*do|introduce\s*yourself)$"
BYE_PATTERN = r"^(bye|goodbye|see\s*you|see\s*ya|cya|have\s*a\s*(good|nice)\s*day)$"


def is_small_talk(question: str) -> bool:
    clean = re.sub(r"[^\w\s]", "", question.strip().lower())
    clean = re.sub(r"\s+", " ", clean).strip()

    if not clean:
        return True

    words = clean.split()
    if len(words) > 7:
        return False

    for pattern in [GREETINGS_PATTERN, HOW_ARE_YOU_PATTERN, THANKS_PATTERN, IDENTITY_PATTERN, BYE_PATTERN]:
        if re.match(pattern, clean):
            return True
    return False


def get_small_talk_reply(question: str, project_id: str = "default") -> str:
    clean = re.sub(r"[^\w\s]", "", question.strip().lower())
    clean = re.sub(r"\s+", " ", clean).strip()

    if re.match(THANKS_PATTERN, clean):
        return "You're very welcome! 😊 Feel free to ask if you have any more questions about the uploaded documents or project."
    elif re.match(BYE_PATTERN, clean):
        return "Goodbye! Have a great day, and feel free to return anytime you need help. 👋"
    elif re.match(HOW_ARE_YOU_PATTERN, clean):
        return "I'm doing great, thank you for asking! 😊 I'm ready to answer any questions about the documents or experience in this project."
    elif re.match(IDENTITY_PATTERN, clean):
        return f"Hey there! 👋 I'm the AI assistant for project '{project_id}'. Ask me anything about the uploaded documents, skills, or experience!"
    else:
        return "Hey there! 👋 I'm the AI assistant for this project. Ask me anything about the uploaded documents, skills, or experience!"


# Node: greeting_reply
async def greeting_reply(state: GraphState) -> Dict[str, Any]:
    question = state["question"]
    project_id = state.get("project_id") or "default"
    reply = get_small_talk_reply(question, project_id)
    return {
        "generation": reply,
        "sources": [],
        "is_grounded": True,
        "rewritten_question": question,
    }


def _strip_think_tags(text: str) -> str:
    """Removes reasoning blocks if a model emits <think>...</think> tags."""
    return re.sub(r"<think>.*?</think>", "", str(text or ""), flags=re.DOTALL).strip()


def _build_combined_context(documents: List[Dict[str, Any]], page_context: str) -> str:
    """Builds clearly separated prompt sections for live webpage and Qdrant document contexts."""
    clean_page = (page_context or "").strip()
    page_section = clean_page if clean_page else "No live webpage context available."

    if documents:
        docs_section = "\n\n".join(
            [f"Document [Source: {doc['source']}]:\n{doc['text']}" for doc in documents]
        )
    else:
        docs_section = "No relevant uploaded documents retrieved."

    return (
        f"--- LIVE WEBPAGE CONTEXT ---\n{page_section}\n\n"
        f"--- UPLOADED DOCUMENTS CONTEXT ---\n{docs_section}"
    )


# Node: rewrite_query
async def rewrite_query(state: GraphState) -> Dict[str, Any]:
    question = state["question"]
    chat_history = state.get("chat_history") or []

    if not chat_history:
        return {"rewritten_question": question}

    recent_history = chat_history[-4:]
    formatted_history = "\n".join(
        [f"{msg.get('role', 'user').capitalize()}: {msg.get('content', '')}" for msg in recent_history]
    )

    prompt = (
        "You are an expert search query rewriter for an educational LMS RAG system.\n"
        "Given the conversation history and a follow-up question, rewrite the follow-up question "
        "into a standalone search query that contains all necessary context for document retrieval.\n\n"
        f"Conversation History:\n{formatted_history}\n\n"
        f"Follow-up Question: {question}\n\n"
        "Rules:\n"
        "- Return ONLY the rewritten search query text.\n"
        "- Do not include explanations, quotes, preambles, or prefixes.\n"
        "- If the question is already clear and standalone, return it as is."
    )
    try:
        response = await groq_llm.ainvoke(prompt)
        rewritten = _strip_think_tags(str(response.content)).strip('"').strip("'")
        return {"rewritten_question": rewritten if rewritten else question}
    except Exception as exc:
        logger.warning(f"Query rewrite failed, using original question: {exc}")
        return {"rewritten_question": question}


# Node a: retrieve
async def retrieve(state: GraphState) -> Dict[str, Any]:
    query_text = state.get("rewritten_question") or state["question"]
    project_id = state.get("project_id") or "default"

    try:
        query_vector = await asyncio.wait_for(embeddings.aembed_query(query_text), timeout=15)

        # Strict multi-tenancy isolation filter on project_id
        query_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="project_id",
                    match=models.MatchValue(value=project_id),
                )
            ]
        )

        def _search_qdrant():
            if hasattr(client, "query_points"):
                search_result = client.query_points(
                    collection_name=COLLECTION_NAME,
                    query=query_vector,
                    query_filter=query_filter,
                    limit=4,
                    with_payload=True,
                )
                return search_result.points
            return client.search(
                collection_name=COLLECTION_NAME,
                query_vector=query_vector,
                query_filter=query_filter,
                limit=4,
                with_payload=True,
            )

        points = await asyncio.wait_for(asyncio.to_thread(_search_qdrant), timeout=15)

        documents = []
        for pt in points:
            payload = pt.payload or {}
            text = payload.get("text", "")
            source = payload.get("source", "unknown")
            if text:
                documents.append({"text": text, "source": source})

        return {"documents": documents}
    except Exception as exc:
        logger.warning(f"Qdrant retrieval failed for project '{project_id}': {exc}")
        return {"documents": []}


# Node b: grade_documents (CRAG)
async def _grade_single_doc(doc: Dict[str, Any], query_text: str) -> bool:
    prompt = (
        "You are a grader assessing relevance of a retrieved document to a user question.\n"
        f"Retrieved Document:\n{doc['text']}\n\n"
        f"User Question:\n{query_text}\n\n"
        "If the document contains information or keywords relevant to the question, respond strictly with 'yes'. "
        "Otherwise, respond strictly with 'no'. Do not include explanations."
    )
    try:
        response = await groq_llm.ainvoke(prompt)
        content = _strip_think_tags(str(response.content)).lower()
        return content.startswith("yes")
    except Exception as exc:
        logger.warning(f"Document grading failed, skipping chunk: {exc}")
        return False


async def grade_documents(state: GraphState) -> Dict[str, Any]:
    query_text = state.get("rewritten_question") or state["question"]
    documents = state.get("documents", [])
    if not documents:
        return {"documents": []}

    decisions = await asyncio.gather(
        *[_grade_single_doc(doc, query_text) for doc in documents]
    )
    relevant_docs = [doc for doc, keep in zip(documents, decisions) if keep]
    return {"documents": relevant_docs}


# Node c: generate
async def generate(state: GraphState) -> Dict[str, Any]:
    documents = state.get("documents", [])
    page_context = (state.get("page_context") or "").strip()
    question = state["question"]

    context_text = _build_combined_context(documents, page_context)

    system_prompt = (
        "You are an accurate AI assistant. Answer the user's question strictly and ONLY using the "
        "combined context provided below (LIVE WEBPAGE CONTEXT and UPLOADED DOCUMENTS CONTEXT).\n"
        "Rules:\n"
        "- Do NOT use any outside knowledge or fabricate facts.\n"
        "- Use information from the Live Webpage Context, the Uploaded Documents Context, or both.\n"
        f"- Only if NEITHER the Live Webpage Context nor the Uploaded Documents Context contains the answer, respond strictly with: \"{FALLBACK_ANSWER}\""
    )

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"{context_text}\n\nQuestion: {question}"),
    ]

    try:
        response = await gemini_llm.ainvoke(messages)
    except Exception as e:
        warning_msg = f"Gemini unavailable, falling back to Groq: {e}"
        print(f"[Generation] Warning: {warning_msg}")
        logger.warning(warning_msg)
        response = await groq_llm.ainvoke(messages)

    generation = _strip_think_tags(str(response.content))

    if FALLBACK_ANSWER.lower() in generation.lower():
        return {"generation": FALLBACK_ANSWER, "sources": []}

    # Collect unique Qdrant PDF sources preserving order, plus "Live Page" if page_context is present
    sources = list(dict.fromkeys([doc["source"] for doc in documents if doc.get("source")]))
    if page_context:
        sources.append("Live Page")

    return {"generation": generation, "sources": sources}


# Node d: check_hallucination (Self-RAG)
async def check_hallucination(state: GraphState) -> Dict[str, Any]:
    documents = state.get("documents", [])
    page_context = (state.get("page_context") or "").strip()
    generation = state.get("generation", "")
    current_sources = list(state.get("sources", []))

    if FALLBACK_ANSWER.lower() in generation.strip().lower():
        return {"is_grounded": True, "sources": []}

    context_text = _build_combined_context(documents, page_context)
    both_present = bool(documents) and bool(page_context)

    if both_present:
        prompt = (
            "You are a grader evaluating an answer against combined facts from LIVE WEBPAGE CONTEXT "
            "and UPLOADED DOCUMENTS CONTEXT.\n"
            f"Combined Facts:\n{context_text}\n\n"
            f"Answer:\n{generation}\n\n"
            "Evaluate two things:\n"
            "1. GROUNDED: Is the answer 100% supported by the combined facts above (yes or no)?\n"
            "2. LIVE_PAGE_USED: Did facts from LIVE WEBPAGE CONTEXT contribute to the answer (yes or no)?\n\n"
            "Respond strictly in two lines with no extra text:\n"
            "GROUNDED: yes|no\n"
            "LIVE_PAGE_USED: yes|no"
        )
    else:
        prompt = (
            "You are a grader assessing whether an answer is grounded in and supported by a set of facts.\n"
            f"Facts:\n{context_text}\n\n"
            f"Answer:\n{generation}\n\n"
            "Is the answer 100% supported by and grounded in the facts provided above? Answer strictly 'yes' or 'no'. "
            "Do not include explanations."
        )

    try:
        response = await groq_llm.ainvoke(prompt)
    except Exception as exc:
        logger.warning(f"Groq hallucination check failed, falling back to Gemini: {exc}")
        response = await gemini_llm.ainvoke(prompt)

    content = _strip_think_tags(str(response.content)).lower()

    if both_present:
        grounded_match = re.search(r"grounded\s*:\s*(yes|no)", content)
        live_page_match = re.search(r"live_page_used\s*:\s*(yes|no)", content)

        is_grounded = (grounded_match.group(1) == "yes") if grounded_match else content.startswith("yes")
        if live_page_match and live_page_match.group(1) == "no":
            current_sources = [s for s in current_sources if s != "Live Page"]
    else:
        is_grounded = content.startswith("yes")

    return {"is_grounded": is_grounded, "sources": current_sources}


# Node e: fallback
async def fallback(state: GraphState) -> Dict[str, Any]:
    return {
        "generation": FALLBACK_ANSWER,
        "sources": [],
        "is_grounded": True,
    }


# Conditional routing functions
def route_intent(state: GraphState) -> str:
    question = state.get("question", "")
    if is_small_talk(question):
        return "greeting_reply"
    return "rewrite_query"


def route_after_grading(state: GraphState) -> str:
    has_docs = bool(state.get("documents"))
    has_page_context = bool((state.get("page_context") or "").strip())
    if has_docs or has_page_context:
        return "generate"
    return "fallback"


def route_after_hallucination(state: GraphState) -> str:
    if state.get("is_grounded", False):
        return END
    return "fallback"


# Build and compile StateGraph
workflow = StateGraph(GraphState)

workflow.add_node("greeting_reply", greeting_reply)
workflow.add_node("rewrite_query", rewrite_query)
workflow.add_node("retrieve", retrieve)
workflow.add_node("grade_documents", grade_documents)
workflow.add_node("generate", generate)
workflow.add_node("check_hallucination", check_hallucination)
workflow.add_node("fallback", fallback)

# Wire edges:
# 1. START -> route_intent (instant greeting reply or rewrite_query)
workflow.add_conditional_edges(
    START,
    route_intent,
    {
        "greeting_reply": "greeting_reply",
        "rewrite_query": "rewrite_query",
    },
)
workflow.add_edge("greeting_reply", END)

# 2. Main RAG Pipeline
workflow.add_edge("rewrite_query", "retrieve")
workflow.add_edge("retrieve", "grade_documents")
workflow.add_conditional_edges(
    "grade_documents",
    route_after_grading,
    {
        "generate": "generate",
        "fallback": "fallback",
    },
)
workflow.add_edge("generate", "check_hallucination")
workflow.add_conditional_edges(
    "check_hallucination",
    route_after_hallucination,
    {
        END: END,
        "fallback": "fallback",
    },
)
workflow.add_edge("fallback", END)

rag_app = workflow.compile()
