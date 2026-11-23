from typing import Any, Dict
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from langchain_groq import ChatGroq
from langgraph.graph import END, START, StateGraph

from app.core.config import settings
from app.core.qdrant import COLLECTION_NAME, client
from app.rag.state import GraphState

# Fast grading & verification LLM
groq_llm = ChatGroq(
    model=settings.GROQ_MODEL,
    temperature=0,
    api_key=settings.GROQ_API_KEY,
)

# High-accuracy answer generation LLM
gemini_llm = ChatGoogleGenerativeAI(
    model=settings.GEMINI_MODEL,
    temperature=0,
    google_api_key=settings.GEMINI_API_KEY,
)

# Query embedding model
embeddings = GoogleGenerativeAIEmbeddings(
    model="gemini-embedding-001",
    google_api_key=settings.GEMINI_API_KEY,
    output_dimensionality=768,
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
    response = await groq_llm.ainvoke(prompt)
    rewritten = str(response.content).strip().strip('"').strip("'")
    return {"rewritten_question": rewritten if rewritten else question}


# Node a: retrieve
async def retrieve(state: GraphState) -> Dict[str, Any]:
    query_text = state.get("rewritten_question") or state["question"]
    query_vector = await embeddings.aembed_query(query_text)

    if hasattr(client, "query_points"):
        search_result = client.query_points(
            collection_name=COLLECTION_NAME,
            query=query_vector,
            limit=4,
            with_payload=True,
        )
        points = search_result.points
    else:
        points = client.search(
            collection_name=COLLECTION_NAME,
            query_vector=query_vector,
            limit=4,
            with_payload=True,
        )

    documents = []
    for pt in points:
        payload = pt.payload or {}
        text = payload.get("text", "")
        source = payload.get("source", "unknown")
        if text:
            documents.append({"text": text, "source": source})

    return {"documents": documents}


# Node b: grade_documents (CRAG)
async def grade_documents(state: GraphState) -> Dict[str, Any]:
    query_text = state.get("rewritten_question") or state["question"]
    documents = state.get("documents", [])
    relevant_docs = []

    for doc in documents:
        prompt = (
            "You are a grader assessing relevance of a retrieved document to a user question.\n"
            f"Retrieved Document:\n{doc['text']}\n\n"
            f"User Question:\n{query_text}\n\n"
            "If the document contains information or keywords relevant to the question, respond strictly with 'yes'. "
            "Otherwise, respond strictly with 'no'. Do not include explanations."
        )
        response = await groq_llm.ainvoke(prompt)
        content = response.content.strip().lower()
        if content.startswith("yes"):
            relevant_docs.append(doc)

    return {"documents": relevant_docs}


# Node c: generate
async def generate(state: GraphState) -> Dict[str, Any]:
    documents = state.get("documents", [])
    question = state["question"]

    context_blocks = [
        f"Document [Source: {doc['source']}]:\n{doc['text']}"
        for doc in documents
    ]
    context_text = "\n\n".join(context_blocks)

    messages = [
        SystemMessage(content="Answer ONLY using the provided context. Do not use outside knowledge."),
        HumanMessage(content=f"Context:\n{context_text}\n\nQuestion: {question}"),
    ]
    response = await gemini_llm.ainvoke(messages)
    generation = str(response.content)

    # Collect unique sources preserving order
    sources = list(dict.fromkeys([doc["source"] for doc in documents if doc.get("source")]))

    return {"generation": generation, "sources": sources}


# Node d: check_hallucination (Self-RAG)
async def check_hallucination(state: GraphState) -> Dict[str, Any]:
    documents = state.get("documents", [])
    generation = state.get("generation", "")

    context_text = "\n\n".join([doc["text"] for doc in documents])

    prompt = (
        "You are a grader assessing whether an answer is grounded in and supported by a set of facts.\n"
        f"Facts:\n{context_text}\n\n"
        f"Answer:\n{generation}\n\n"
        "Is the answer 100% supported by and grounded in the facts provided above? Answer strictly 'yes' or 'no'. "
        "Do not include explanations."
    )
    response = await groq_llm.ainvoke(prompt)
    content = response.content.strip().lower()

    is_grounded = content.startswith("yes")
    return {"is_grounded": is_grounded}


# Node e: fallback
async def fallback(state: GraphState) -> Dict[str, Any]:
    return {
        "generation": "I do not have that information in our official documents.",
        "sources": [],
        "is_grounded": True,
    }


# Conditional routing functions
def route_after_grading(state: GraphState) -> str:
    if state.get("documents") and len(state["documents"]) > 0:
        return "generate"
    return "fallback"


def route_after_hallucination(state: GraphState) -> str:
    if state.get("is_grounded", False):
        return END
    return "fallback"


# Build and compile StateGraph
workflow = StateGraph(GraphState)

workflow.add_node("rewrite_query", rewrite_query)
workflow.add_node("retrieve", retrieve)
workflow.add_node("grade_documents", grade_documents)
workflow.add_node("generate", generate)
workflow.add_node("check_hallucination", check_hallucination)
workflow.add_node("fallback", fallback)

# Wire edges: START -> rewrite_query -> retrieve -> grade_documents -> ...
workflow.add_edge(START, "rewrite_query")
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
