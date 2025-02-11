"""
Script to serve RAG system combining vector search with DeepSeek R1.
"""

import argparse
import logging
import os
import pickle
from typing import List, Optional

import chromadb
import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests
from sentence_transformers import SentenceTransformer
import torch
import uvicorn

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(title='RAG System with DeepSeek R1')

# Global variables
embedding_model = None
collection = None
vllm_endpoint = None

class QueryRequest(BaseModel):
    query: str
    n_results: Optional[int] = 3
    temperature: Optional[float] = 0.7

class SearchResult(BaseModel):
    content: str
    id: str
    name: str
    split: str
    source: str
    similarity: float

class RAGResponse(BaseModel):
    answer: str
    sources: List[SearchResult]

def encode_query(query: str) -> np.ndarray:
    """Encode query text using sentence transformer."""
    global embedding_model
    
    # Compute embedding
    embedding = embedding_model.encode([query])[0]
    return embedding

def query_collection(query_embedding: np.ndarray,
                    n_results: int = 3) -> List[SearchResult]:
    """Query the collection and return top matches."""
    global collection
    
    results = collection.query(
        query_embeddings=[query_embedding.tolist()],
        n_results=n_results,
        include=['metadatas', 'distances', 'documents']
    )
    
    # Get results
    documents = results['documents'][0]
    metadatas = results['metadatas'][0]
    distances = results['distances'][0]
    
    # Convert distances to similarities
    similarities = [1 - (d / 2) for d in distances]
    
    return [
        SearchResult(
            content=doc,
            id=meta['id'],
            name=meta['name'],
            split=meta['split'],
            source=meta['source'],
            similarity=sim
        )
        for doc, meta, sim in zip(documents, metadatas, similarities)
    ]

def generate_prompt(query: str, context_docs: List[SearchResult]) -> str:
    """Generate prompt for DeepSeek R1."""
    context = "\n\n".join([
        f"Document: {doc.name} (Source: {doc.source})\nContent: {doc.content}"
        for doc in context_docs
    ])
    
    return f"""You are a helpful AI assistant that answers questions about legal documents from the Pile of Law dataset.
Below is some relevant context from legal documents, followed by a question.
Please answer the question based on the context provided. If you cannot find the answer in the context,
say so - do not make up information.

Context:
{context}

Question: {query}

Answer:"""

async def query_llm(prompt: str, temperature: float = 0.7) -> str:
    """Query DeepSeek R1 through vLLM endpoint."""
    global vllm_endpoint
    
    try:
        response = requests.post(
            f"{vllm_endpoint}/v1/completions",
            json={
                "prompt": prompt,
                "temperature": temperature,
                "max_tokens": 512,
                "stop": None
            },
            timeout=30
        )
        response.raise_for_status()
        return response.json()['choices'][0]['text'].strip()
    except Exception as e:
        logger.error(f"Error querying LLM: {str(e)}")
        raise HTTPException(status_code=500, detail="Error querying language model")

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
        answer = await query_llm(prompt, request.temperature)
        
        return RAGResponse(answer=answer, sources=results)
    
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

def main():
    parser = argparse.ArgumentParser(description='Serve RAG system')
    parser.add_argument('--host',
                       type=str,
                       default='0.0.0.0',
                       help='Host to serve on')
    parser.add_argument('--port',
                       type=int,
                       default=8001,
                       help='Port to serve on')
    parser.add_argument('--collection-name',
                       type=str,
                       default='legal_docs',
                       help='ChromaDB collection name')
    parser.add_argument('--persist-dir',
                       type=str,
                       default='/vectordb/chroma',
                       help='Directory where ChromaDB is persisted')
    parser.add_argument('--model-name',
                       type=str,
                       default='sentence-transformers/all-mpnet-base-v2',
                       help='Sentence transformer model name')
    parser.add_argument('--vllm-endpoint',
                       type=str,
                       required=True,
                       help='Endpoint for vLLM service')
    
    args = parser.parse_args()
    
    # Initialize global variables
    global embedding_model, collection, vllm_endpoint
    
    # Set vLLM endpoint
    vllm_endpoint = args.vllm_endpoint.rstrip('/')
    
    # Load embedding model
    logger.info(f'Loading embedding model: {args.model_name}')
    embedding_model = SentenceTransformer(args.model_name)
    
    # Initialize ChromaDB
    logger.info(f'Connecting to ChromaDB at {args.persist_dir}')
    client = chromadb.PersistentClient(path=args.persist_dir)
    
    try:
        collection = client.get_collection(name=args.collection_name)
        logger.info(f'Connected to collection: {args.collection_name}')
        logger.info(f'Total documents in collection: {collection.count()}')
    except ValueError as e:
        logger.error(f'Error: {str(e)}')
        logger.error('Make sure the collection exists and the persist_dir is correct.')
        raise
    
    # Start server
    uvicorn.run(app, host=args.host, port=args.port)

if __name__ == '__main__':
    main() 