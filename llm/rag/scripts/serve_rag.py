"""
Script to serve RAG system combining vector search with DeepSeek R1.
"""

import argparse
import logging
import os
import pickle
import time
from typing import Any, Dict, List, Optional
import uuid

import chromadb
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.responses import HTMLResponse
import numpy as np
from pydantic import BaseModel
import requests
import torch
import uvicorn

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(title='RAG System with DeepSeek R1')

# Global variables
collection = None
generator_endpoint = None  # For text generation
embed_endpoint = None  # For embeddings

# Dictionary to store in-progress LLM queries
active_requests = {}


class QueryRequest(BaseModel):
    query: str
    n_results: Optional[int] = 3
    temperature: Optional[float] = 0.7


class DocumentsOnlyRequest(BaseModel):
    query: str
    n_results: Optional[int] = 3


class StartLLMRequest(BaseModel):
    request_id: str
    temperature: Optional[float] = 0.7


class SearchResult(BaseModel):
    content: str
    name: str
    split: str
    source: str
    similarity: float


class RAGResponse(BaseModel):
    answer: str
    sources: List[SearchResult]
    thinking_process: str  # Add thinking process to response


class DocumentsOnlyResponse(BaseModel):
    sources: List[SearchResult]
    request_id: str


class LLMStatusResponse(BaseModel):
    status: str  # "pending", "completed", "error"
    answer: Optional[str] = None
    thinking_process: Optional[str] = None
    error: Optional[str] = None


def encode_query(query: str) -> np.ndarray:
    """Encode query text using vLLM embeddings endpoint."""
    global embed_endpoint

    try:
        response = requests.post(f"{embed_endpoint}/v1/embeddings",
                                 json={
                                     "model": "/tmp/embedding_model",
                                     "input": [query]
                                 },
                                 timeout=30)
        response.raise_for_status()

        result = response.json()
        if 'data' not in result:
            raise ValueError(f"Unexpected response format: {result}")

        return np.array(result['data'][0]['embedding'])

    except Exception as e:
        logger.error(f"Error computing query embedding: {str(e)}")
        raise HTTPException(status_code=500,
                            detail="Error computing query embedding")


def query_collection(query_embedding: np.ndarray,
                     n_results: int = 10) -> List[SearchResult]:
    """Query the collection and return top matches."""
    global collection

    # Request more results initially to account for potential duplicates
    max_results = min(n_results * 2, 20)  # Get more results but cap at 20

    results = collection.query(query_embeddings=[query_embedding.tolist()],
                               n_results=max_results,
                               include=['metadatas', 'distances', 'documents'])

    # Get results
    documents = results['documents'][0]
    metadatas = results['metadatas'][0]
    distances = results['distances'][0]

    # Convert distances to similarities
    similarities = [1 - (d / 2) for d in distances]

    # Create a set to track unique content
    seen_content = set()
    unique_results = []

    for doc, meta, sim in zip(documents, metadatas, similarities):
        # Use content as the uniqueness key
        if doc not in seen_content:
            seen_content.add(doc)
            logger.info(f"Found {meta} with similarity {sim}")
            logger.info(f"Content: {doc}")
            unique_results.append((doc, meta, sim))

            # Break if we have enough unique results
            if len(unique_results) >= n_results:
                break

    return [
        SearchResult(content=doc,
                     name=meta['name'],
                     split=meta['split'],
                     source=meta['source'],
                     similarity=sim) for doc, meta, sim in unique_results
    ]


def generate_prompt(query: str, context_docs: List[SearchResult]) -> str:
    """Generate prompt for DeepSeek R1."""
    # Format context with clear document boundaries
    context = "\n\n".join([
        f"[Document {i+1} begin]\nSource: {doc.source}\nContent: {doc.content}\n[Document {i+1} end]"
        for i, doc in enumerate(context_docs)
    ])

    return f"""# The following contents are search results from legal documents and related discussions:
{context}

You are a helpful AI assistant analyzing legal documents and related content. When responding, please follow these guidelines:
- In the search results provided, each document is formatted as [Document X begin]...[Document X end], where X represents the numerical index of each document.
- Cite your documents using [citation:X] format where X is the document number, placing citations immediately after the relevant information.
- Include citations throughout your response, not just at the end.
- If information comes from multiple documents, use multiple citations like [citation:1][citation:2].
- Not all search results may be relevant - evaluate and use only pertinent information.
- Structure longer responses into clear paragraphs or sections for readability.
- If you cannot find the answer in the provided documents, say so - do not make up information.
- Some documents may be informal discussions or reddit posts - adjust your interpretation accordingly.
- Put citation as much as possible in your response. If you mention two Documents, mention as Document X and Document Y, instead of Document X and Y.

First, explain your thinking process between <think> tags.
Then provide your final answer after the thinking process.

# Question:
{query}

Let's approach this step by step:"""


async def query_llm(prompt: str, temperature: float = 0.7) -> tuple[str, str]:
    """Query DeepSeek R1 through vLLM endpoint and return thinking process and answer."""
    global generator_endpoint

    try:
        response = requests.post(f"{generator_endpoint}/v1/chat/completions",
                                 json={
                                     "model": "/tmp/generation_model",
                                     "messages": [{
                                         "role": "user",
                                         "content": prompt
                                     }],
                                     "temperature": temperature,
                                     "max_tokens": 2048,
                                     "stop": None
                                 },
                                 timeout=120)
        response.raise_for_status()

        logger.info(f"Response: {response.json()}")

        full_response = response.json(
        )['choices'][0]['message']['content'].strip()

        # Split response into thinking process and answer
        parts = full_response.split("</think>")
        if len(parts) > 1:
            thinking = parts[0].replace("<think>", "").strip()
            answer = parts[1].strip()
        else:
            thinking = ""
            answer = full_response

        return thinking, answer

    except Exception as e:
        logger.error(f"Error querying LLM: {str(e)}")
        raise HTTPException(status_code=500,
                            detail="Error querying language model")


@app.post('/rag', response_model=RAGResponse)
async def rag_query(request: QueryRequest):
    """RAG endpoint combining vector search with DeepSeek R1."""
    try:
        # Encode query
        query_embedding = encode_query(request.query)

        # Get relevant documents
        results = query_collection(query_embedding, request.n_results)

        # Generate prompt
        prompt = generate_prompt(request.query, results)

        # Get LLM response
        thinking, answer = await query_llm(prompt, request.temperature)

        return RAGResponse(answer=answer,
                           sources=results,
                           thinking_process=thinking)

    except Exception as e:
        logger.error(f"Error processing RAG query: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get('/health')
async def health_check():
    """Health check endpoint."""
    return {
        'status': 'healthy',
        'collection_size': collection.count() if collection else 0
    }


@app.get('/', response_class=HTMLResponse)
async def get_search_page():
    """Serve a simple search interface."""
    template_path = os.path.join(os.path.dirname(__file__), 'templates',
                                 'index.html')
    try:
        with open(template_path, 'r') as f:
            return f.read()
    except FileNotFoundError:
        raise HTTPException(
            status_code=500,
            detail=f"Template file not found at {template_path}")


@app.post('/documents', response_model=DocumentsOnlyResponse)
async def get_documents(request: DocumentsOnlyRequest):
    """Get relevant documents for a query without LLM processing."""
    try:
        # Encode query
        query_embedding = encode_query(request.query)

        # Get relevant documents
        results = query_collection(query_embedding, request.n_results)

        # Generate a unique request ID
        request_id = str(uuid.uuid4())

        # Store the request data for later LLM processing
        active_requests[request_id] = {
            "query": request.query,
            "results": results,
            "status": "documents_ready",
            "timestamp": time.time()
        }

        # Clean up old requests (older than 30 minutes)
        current_time = time.time()
        expired_requests = [
            req_id for req_id, data in active_requests.items()
            if current_time - data["timestamp"] > 1800
        ]
        for req_id in expired_requests:
            active_requests.pop(req_id, None)

        return DocumentsOnlyResponse(sources=results, request_id=request_id)

    except Exception as e:
        logger.error(f"Error retrieving documents: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post('/process_llm', response_model=LLMStatusResponse)
async def process_llm(request: StartLLMRequest):
    """Process a query with the LLM using previously retrieved documents."""
    request_id = request.request_id

    # Check if the request exists and is ready for LLM processing
    if request_id not in active_requests or active_requests[request_id][
            "status"] != "documents_ready":
        raise HTTPException(status_code=404,
                            detail="Request not found or documents not ready")

    # Mark the request as in progress
    active_requests[request_id]["status"] = "llm_processing"

    try:
        # Get stored data
        query = active_requests[request_id]["query"]
        results = active_requests[request_id]["results"]

        # Generate prompt
        prompt = generate_prompt(query, results)

        # Get LLM response
        thinking, answer = await query_llm(prompt, request.temperature)

        # Store the response and mark as completed
        active_requests[request_id]["status"] = "completed"
        active_requests[request_id]["thinking"] = thinking
        active_requests[request_id]["answer"] = answer
        active_requests[request_id]["timestamp"] = time.time()

        return LLMStatusResponse(status="completed",
                                 answer=answer,
                                 thinking_process=thinking)

    except Exception as e:
        # Mark as error
        active_requests[request_id]["status"] = "error"
        active_requests[request_id]["error"] = str(e)
        active_requests[request_id]["timestamp"] = time.time()

        logger.error(f"Error processing LLM request: {str(e)}")
        return LLMStatusResponse(status="error", error=str(e))


@app.get('/llm_status/{request_id}', response_model=LLMStatusResponse)
async def get_llm_status(request_id: str):
    """Get the status of an LLM request."""
    if request_id not in active_requests:
        raise HTTPException(status_code=404, detail="Request not found")

    request_data = active_requests[request_id]

    if request_data["status"] == "completed":
        return LLMStatusResponse(status="completed",
                                 answer=request_data["answer"],
                                 thinking_process=request_data["thinking"])
    elif request_data["status"] == "error":
        return LLMStatusResponse(status="error",
                                 error=request_data.get("error",
                                                        "Unknown error"))
    else:
        return LLMStatusResponse(status="pending")


def main():
    parser = argparse.ArgumentParser(description='Serve RAG system')
    parser.add_argument('--host',
                        type=str,
                        default='0.0.0.0',
                        help='Host to serve on')
    parser.add_argument('--port',
                        type=int,
                        default=8000,
                        help='Port to serve on')
    parser.add_argument('--collection-name',
                        type=str,
                        default='legal_docs',
                        help='ChromaDB collection name')
    parser.add_argument('--persist-dir',
                        type=str,
                        default='/vectordb/chroma',
                        help='Directory where ChromaDB is persisted')
    parser.add_argument('--generator-endpoint',
                        type=str,
                        required=True,
                        help='Endpoint for text generation service')
    parser.add_argument('--embed-endpoint',
                        type=str,
                        required=True,
                        help='Endpoint for embeddings service')

    args = parser.parse_args()

    # Initialize global variables
    global collection, generator_endpoint, embed_endpoint

    # Set endpoints
    generator_endpoint = args.generator_endpoint.rstrip('/')
    embed_endpoint = args.embed_endpoint.rstrip('/')

    # Initialize ChromaDB
    logger.info(f'Connecting to ChromaDB at {args.persist_dir}')
    client = chromadb.PersistentClient(path=args.persist_dir)

    try:
        collection = client.get_collection(name=args.collection_name)
        logger.info(f'Connected to collection: {args.collection_name}')
        logger.info(f'Total documents in collection: {collection.count()}')
    except ValueError as e:
        logger.error(f'Error: {str(e)}')
        logger.error(
            'Make sure the collection exists and the persist_dir is correct.')
        raise

    # Start server
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == '__main__':
    main()
