from contextlib import asynccontextmanager
from datetime import datetime
import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pydantic import Field
import redis
from redisvl.index import SearchIndex
from redisvl.query import VectorQuery
from sentence_transformers import SentenceTransformer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

redis_client = None
index = None
model = None


class SearchRequest(BaseModel):
    query: str
    k: int = Field(default=10, ge=1, le=100)
    filters: Optional[Dict[str, Any]] = None


class PaperResult(BaseModel):
    id: str
    title: str
    abstract: str
    authors: str
    venue: str
    year: int
    n_citation: int
    score: float


class SearchResponse(BaseModel):
    results: List[PaperResult]
    total: int
    time_ms: float


@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client, index, model

    redis_client = redis.Redis(host=os.getenv("REDIS_HOST"),
                               port=int(os.getenv("REDIS_PORT")),
                               username=os.getenv("REDIS_USER"),
                               password=os.getenv("REDIS_PASSWORD"),
                               decode_responses=True)
    redis_client.ping()
    logger.info("Connected to Redis")

    index = SearchIndex.from_yaml("config/redis_schema_papers.yaml",
                                  redis_client=redis_client)
    try:
        index.create(overwrite=False)
    except Exception as e:
        if "already exists" not in str(e).lower():
            raise

    model = SentenceTransformer(os.getenv("MODEL_NAME"), trust_remote_code=True)
    logger.info("Ready")

    yield

    if redis_client:
        redis_client.close()


app = FastAPI(title="Paper Search API", lifespan=lifespan)
app.add_middleware(CORSMiddleware,
                   allow_origins=["*"],
                   allow_methods=["*"],
                   allow_headers=["*"])


@app.get("/health")
async def health():
    try:
        redis_client.ping()
        return {"status": "healthy", "papers": index.info().get("num_docs", 0)}
    except:
        return {"status": "unhealthy"}


@app.post("/search", response_model=SearchResponse)
async def search(request: SearchRequest):
    start = datetime.now()
    embedding = model.encode(request.query, normalize_embeddings=True).tolist()

    query = VectorQuery(vector=embedding,
                        vector_field_name="paper_embedding",
                        return_fields=[
                            "title", "abstract", "authors", "venue", "year",
                            "n_citation"
                        ],
                        num_results=request.k)

    if request.filters:
        filters = []
        for field, value in request.filters.items():
            if field == "year" and isinstance(value, dict):
                if "min" in value and "max" in value:
                    filters.append(f"@year:[{value['min']} {value['max']}]")
            elif field == "venue":
                filters.append(f"@venue:{{{value}}}")
        if filters:
            query.set_filter(" ".join(filters))

    results = index.query(query)

    papers = []
    for r in results:
        abstract = r.get('abstract', '')
        if len(abstract) > 200:
            abstract = abstract[:200] + "..."

        papers.append(
            PaperResult(id=r.get('id', ''),
                        title=r.get('title', ''),
                        abstract=abstract,
                        authors=r.get('authors', ''),
                        venue=r.get('venue', ''),
                        year=int(r.get('year', 0)),
                        n_citation=int(r.get('n_citation', 0)),
                        score=round(1 - float(r.get('vector_score', 0)), 3)))

    time_ms = (datetime.now() - start).total_seconds() * 1000
    return SearchResponse(results=papers,
                          total=len(papers),
                          time_ms=round(time_ms, 2))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
