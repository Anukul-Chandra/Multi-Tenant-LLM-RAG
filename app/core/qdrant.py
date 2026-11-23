from qdrant_client import QdrantClient, models
from app.core.config import settings

COLLECTION_NAME = "swiss_lms_docs"
VECTOR_SIZE = 768

client = QdrantClient(
    url=settings.QDRANT_URL,
    api_key=settings.QDRANT_API_KEY,
)


def ensure_collection_exists() -> None:
    """
    Checks if the Swiss LMS collection exists in Qdrant,
    creating it with Cosine distance and 768-dim vectors if it does not,
    and ensures keyword payload indexes exist on project_id and source.
    """
    if not settings.QDRANT_URL or not settings.QDRANT_API_KEY:
        raise ValueError("Qdrant credentials missing. Please set QDRANT_URL and QDRANT_API_KEY.")

    if not client.collection_exists(COLLECTION_NAME):
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=models.VectorParams(
                size=VECTOR_SIZE,
                distance=models.Distance.COSINE,
            ),
        )

    # Create keyword payload indexes on project_id and source
    for field_name in ["project_id", "source"]:
        try:
            client.create_payload_index(
                collection_name=COLLECTION_NAME,
                field_name=field_name,
                field_schema=models.PayloadSchemaType.KEYWORD,
            )
        except Exception:
            pass
