import asyncio
import logging
import re
import time
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

STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "can", "may", "might", "must", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "about", "as", "into", "through", "and", "or",
    "but", "if", "so", "than", "that", "this", "these", "those", "it", "its",
    "he", "she", "they", "we", "you", "i", "me", "my", "your", "his", "her",
    "their", "our", "what", "which", "who", "whom", "whose", "where", "when",
    "why", "how", "all", "any", "some", "no", "not", "tell", "give", "show",
    "list", "please", "does", "more", "explain", "describe",
}

# Ultra-fast primary generator LLM
fast_generator_llm = ChatGroq(
    model="llama-3.1-8b-instant",
    temperature=0.2,
    max_tokens=350,
    api_key=settings.GROQ_API_KEY,
    timeout=15,
    max_retries=0,
)

# Grading & verification LLM (for pure-Qdrant CRAG / Self-RAG mode)
groq_llm = ChatGroq(
    model=settings.GROQ_MODEL,
    temperature=0,
    api_key=settings.GROQ_API_KEY,
    timeout=15,
    max_retries=0,
)

# Fallback answer generation LLM
gemini_llm = ChatGoogleGenerativeAI(
    model=settings.GEMINI_MODEL,
    temperature=0,
    google_api_key=settings.GEMINI_API_KEY,
    timeout=15,
    max_retries=0,
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


def _filter_page_context(page_context: str, question: str, max_chars: int = 3500) -> str:
    """
    0ms local Python smart filter: splits page_context into overlapping ~600-char chunks,
    scores by case-insensitive keyword overlap with the question (excluding stopwords),
    and returns the intro chunk + top 4 scoring chunks (capped at max_chars).
    """
    clean_page = (page_context or "").strip()
    if not clean_page:
        return ""
    if len(clean_page) <= max_chars:
        return clean_page

    # Build overlapping ~600-character chunks (step=480, overlap=120)
    chunk_size = 600
    step = 480
    chunks: List[str] = []
    start = 0
    text_len = len(clean_page)
    while start < text_len:
        end = min(start + chunk_size, text_len)
        chunks.append(clean_page[start:end])
        if end >= text_len:
            break
        start += step

    if not chunks:
        return clean_page[:max_chars]

    # Extract non-stopword query keywords
    raw_tokens = re.findall(r"[a-z0-9]+", (question or "").lower())
    keywords = [t for t in dict.fromkeys(raw_tokens) if t not in STOPWORDS and len(t) > 1]

    # Score remaining chunks (idx >= 1); chunk 0 (intro) is always included
    scored_rest = []
    for idx in range(1, len(chunks)):
        chunk_lower = chunks[idx].lower()
        chunk_words = set(re.findall(r"[a-z0-9]+", chunk_lower))
        unique_hits = 0
        total_hits = 0
        for kw in keywords:
            if kw in chunk_words:
                unique_hits += 1
                total_hits += 2
            elif len(kw) >= 4 and kw in chunk_lower:
                unique_hits += 1
                total_hits += 1
        scored_rest.append((unique_hits, total_hits, -idx, idx))

    scored_rest.sort(reverse=True)
    selected_indices = {0}
    for _, _, _, idx in scored_rest[:4]:
        selected_indices.add(idx)

    # Preserve document order and respect max_chars budget
    selected_parts: List[str] = []
    running_len = 0
    for idx in sorted(selected_indices):
        part = chunks[idx].strip()
        if not part:
            continue
        sep_len = 5 if selected_parts else 0  # "\n...\n"
        if running_len + sep_len + len(part) > max_chars:
            remaining = max_chars - running_len - sep_len
            if remaining > 80:
                selected_parts.append(part[:remaining])
            break
        selected_parts.append(part)
        running_len += sep_len + len(part)

    return "\n...\n".join(selected_parts)


def _build_combined_context(
    documents: List[Dict[str, Any]],
    page_context: str,
    question: str = "",
) -> str:
    """Builds clearly separated prompt sections for filtered live webpage and Qdrant document contexts."""
    filtered_page = _filter_page_context(page_context, question)
    page_section = filtered_page if filtered_page else "No live webpage context available."

    uploaded_docs = [doc for doc in documents if doc.get("source") != "Live Page"]
    if uploaded_docs:
        docs_section = "\n\n".join(
            [f"Document [Source: {doc['source']}]:\n{doc['text']}" for doc in uploaded_docs]
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
    page_context = (state.get("page_context") or "").strip()

    # Keep Hybrid Mode single-pass (0 extra LLM calls before generate)
    if not chat_history or page_context:
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
    t0 = time.perf_counter()
    query_text = state.get("rewritten_question") or state["question"]
    project_id = state.get("project_id") or "default"
    documents: List[Dict[str, Any]] = []

    async def _run_vector_search() -> List[Dict[str, Any]]:
        query_vector = await embeddings.aembed_query(query_text)

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
                    limit=2,
                    with_payload=True,
                )
                return search_result.points
            return client.search(
                collection_name=COLLECTION_NAME,
                query_vector=query_vector,
                query_filter=query_filter,
                limit=2,
                with_payload=True,
            )

        points = await asyncio.to_thread(_search_qdrant)
        docs = []
        for pt in points:
            payload = pt.payload or {}
            text = payload.get("text", "")
            source = payload.get("source", "unknown")
            if text:
                docs.append({"text": text, "source": source})
        return docs

    try:
        # Strict 5-second timeout so vector search never stalls the response
        documents = await asyncio.wait_for(_run_vector_search(), timeout=5.0)
    except Exception as exc:
        logger.warning(f"Qdrant retrieval failed or timed out for project '{project_id}': {exc}")

    qdrant_ms = round((time.perf_counter() - t0) * 1000, 1)
    return {"documents": documents, "qdrant_ms": qdrant_ms}


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
    t0 = time.perf_counter()
    documents = state.get("documents", [])
    page_context = (state.get("page_context") or "").strip()
    question = state["question"]

    context_text = _build_combined_context(documents, page_context, question=question)

    system_prompt = (
        "You are an accurate, concise AI assistant. Answer the user's question strictly and ONLY using the "
        "combined context provided below (LIVE WEBPAGE CONTEXT and UPLOADED DOCUMENTS CONTEXT).\n"
        "Rules:\n"
        "- Do NOT use any outside knowledge or fabricate facts.\n"
        "- Use information from the Live Webpage Context, the Uploaded Documents Context, or both.\n"
        "- Keep answers concise, structured, and easy to read.\n"
        f"- Only if NEITHER the Live Webpage Context nor the Uploaded Documents Context contains the answer, respond strictly with: \"{FALLBACK_ANSWER}\""
    )

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"{context_text}\n\nQuestion: {question}"),
    ]

    try:
        response = await fast_generator_llm.ainvoke(messages)
    except Exception as e:
        warning_msg = f"Groq primary generator unavailable, falling back to Gemini: {e}"
        print(f"[Generation] Warning: {warning_msg}")
        logger.warning(warning_msg)
        response = await gemini_llm.ainvoke(messages)

    llm_ms = round((time.perf_counter() - t0) * 1000, 1)
    generation = _strip_think_tags(str(response.content))

    if FALLBACK_ANSWER.lower() in generation.lower():
        return {"generation": FALLBACK_ANSWER, "sources": [], "is_grounded": True, "llm_ms": llm_ms}

    # Collect unique Qdrant PDF sources preserving order, plus "Live Page" if page_context is present
    uploaded_docs = [doc for doc in documents if doc.get("source") != "Live Page"]
    sources = list(dict.fromkeys([doc["source"] for doc in uploaded_docs if doc.get("source")]))
    if page_context:
        sources.append("Live Page")
        return {"generation": generation, "sources": sources, "is_grounded": True, "llm_ms": llm_ms}

    return {"generation": generation, "sources": sources, "llm_ms": llm_ms}


# Node d: check_hallucination (Self-RAG)
async def check_hallucination(state: GraphState) -> Dict[str, Any]:
    documents = state.get("documents", [])
    uploaded_docs = [doc for doc in documents if doc.get("source") != "Live Page"]
    page_context = (state.get("page_context") or "").strip()
    question = state.get("question", "")
    generation = state.get("generation", "")
    current_sources = list(state.get("sources", []))

    if FALLBACK_ANSWER.lower() in generation.strip().lower():
        return {"is_grounded": True, "sources": []}

    context_text = _build_combined_context(documents, page_context, question=question)
    both_present = bool(uploaded_docs) and bool(page_context)

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


def route_after_retrieve(state: GraphState) -> str:
    if (state.get("page_context") or "").strip():
        return "generate"
    return "grade_documents"


def route_after_grading(state: GraphState) -> str:
    has_docs = bool(state.get("documents"))
    has_page_context = bool((state.get("page_context") or "").strip())
    if has_docs or has_page_context:
        return "generate"
    return "fallback"


def route_after_generation(state: GraphState) -> str:
    if (state.get("page_context") or "").strip():
        return END
    return "check_hallucination"


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

# 2. Main RAG Pipeline (Fast hybrid single-pass when page_context is present; full CRAG + Self-RAG otherwise)
workflow.add_edge("rewrite_query", "retrieve")
workflow.add_conditional_edges(
    "retrieve",
    route_after_retrieve,
    {
        "generate": "generate",
        "grade_documents": "grade_documents",
    },
)
workflow.add_conditional_edges(
    "grade_documents",
    route_after_grading,
    {
        "generate": "generate",
        "fallback": "fallback",
    },
)
workflow.add_conditional_edges(
    "generate",
    route_after_generation,
    {
        END: END,
        "check_hallucination": "check_hallucination",
    },
)
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
