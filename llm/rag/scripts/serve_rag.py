"""
Script to serve RAG system combining vector search with DeepSeek R1.
"""

import argparse
import logging
import os
import pickle
from typing import List, Optional

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


class QueryRequest(BaseModel):
    query: str
    n_results: Optional[int] = 3
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
        SearchResult(
            content=doc,
            name=meta['name'],
            split=meta['split'],
            source=meta['source'],
            similarity=sim)
        for doc, meta, sim in unique_results
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
    return """
    <html>
        <head>
            <title>SkyPilot Legal RAG System</title>
            <style>
                * { box-sizing: border-box; margin: 0; padding: 0; }
                body { 
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    line-height: 1.6;
                    background-color: #f5f5f5;
                    color: #333;
                    min-height: 100vh;
                }
                .container {
                    max-width: 1200px;
                    margin: 0 auto;
                    padding: 2rem;
                }
                .search-container {
                    background: white;
                    padding: 2rem;
                    border-radius: 10px;
                    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
                    margin-bottom: 2rem;
                    text-align: center;
                }
                h1 {
                    color: #2c3e50;
                    margin-bottom: 1.5rem;
                    font-size: 2.5rem;
                }
                .search-box {
                    display: flex;
                    gap: 10px;
                    max-width: 800px;
                    margin: 0 auto;
                }
                input {
                    flex: 1;
                    padding: 12px 20px;
                    border: 2px solid #e0e0e0;
                    border-radius: 25px;
                    font-size: 16px;
                    transition: all 0.3s ease;
                }
                input:focus {
                    outline: none;
                    border-color: #3498db;
                    box-shadow: 0 0 5px rgba(52, 152, 219, 0.3);
                }
                button {
                    padding: 12px 30px;
                    background: #3498db;
                    color: white;
                    border: none;
                    border-radius: 25px;
                    cursor: pointer;
                    font-size: 16px;
                    transition: background 0.3s ease;
                }
                button:hover {
                    background: #2980b9;
                }
                .results-container {
                    display: grid;
                    gap: 2rem;
                }
                .result-section {
                    background: white;
                    border-radius: 10px;
                    padding: 1.5rem;
                    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
                }
                .section-title {
                    color: #2c3e50;
                    margin-bottom: 1rem;
                    font-size: 1.5rem;
                    border-bottom: 2px solid #e0e0e0;
                    padding-bottom: 0.5rem;
                }
                .source-document {
                    background: #f8f9fa;
                    padding: 1rem;
                    margin-bottom: 1rem;
                    border-radius: 5px;
                    border-left: 4px solid #3498db;
                    white-space: pre-wrap;
                }
                .source-header {
                    font-weight: bold;
                    color: #2c3e50;
                    margin-bottom: 0.5rem;
                }
                .source-url {
                    color: #3498db;
                    text-decoration: underline;
                    word-break: break-all;
                    margin-bottom: 0.5rem;
                }
                .thinking-process {
                    background: #fff3e0;
                    padding: 1rem;
                    border-radius: 5px;
                    border-left: 4px solid #ff9800;
                    white-space: pre-wrap;
                }
                .final-answer {
                    background: #e8f5e9;
                    padding: 1rem;
                    border-radius: 5px;
                    border-left: 4px solid #4caf50;
                    white-space: pre-wrap;
                }
                #loading {
                    display: none;
                    text-align: center;
                    margin: 2rem 0;
                    font-size: 1.2rem;
                    color: #666;
                }
                .similarity-score {
                    color: #666;
                    font-size: 0.9rem;
                    margin-top: 0.5rem;
                }
                /* Add new styles for citations */
                .citation {
                    color: #3498db;
                    cursor: pointer;
                    text-decoration: underline;
                }
                .citation:hover {
                    color: #2980b9;
                }
                .highlighted-source {
                    animation: highlight 2s;
                }
                @keyframes highlight {
                    0% { background-color: #fff3cd; }
                    100% { background-color: #f8f9fa; }
                }
                .disclaimer {
                    color: #666;
                    font-size: 1rem;
                    margin-bottom: 2rem;
                    font-style: italic;
                }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="search-container">
                    <h1>SkyPilot Legal RAG System</h1>
                    <div class="search-box">
                        <input type="text" id="searchInput" placeholder="Ask a question about legal documents..."
                            onkeypress="if(event.key === 'Enter') search()">
                        <button onclick="search()">Ask</button>
                    </div>
                    <!-- <p class="disclaimer">This website is just a demonstration that SkyPilot can streamline the RAG generation with cheaper and faster processing. This does not and would not give any legal advice.</p> -->
                </div>
                <div id="loading">Processing your question...</div>
                <div id="results" class="results-container"></div>
            </div>
            
            <script>
            function escapeHtml(unsafe) {
                return unsafe
                    .replace(/&/g, "&amp;")
                    .replace(/</g, "&lt;")
                    .replace(/>/g, "&gt;")
                    .replace(/"/g, "&quot;")
                    .replace(/'/g, "&#039;");
            }

            function highlightSource(docNumber) {
                // Remove previous highlights
                document.querySelectorAll('.highlighted-source').forEach(el => {
                    el.classList.remove('highlighted-source');
                });
                
                // Add highlight to clicked source
                const sourceElement = document.querySelector(`[data-doc-number="${docNumber}"]`);
                if (sourceElement) {
                    sourceElement.classList.add('highlighted-source');
                    sourceElement.scrollIntoView({ behavior: 'smooth', block: 'center' });
                }
            }

            function processCitations(text) {
                // Handle both [citation:X] and Document X formats
                return text
                    .replace(/\[citation:(\d+)\]/g, (match, docNumber) => {
                        return `<span class="citation" onclick="highlightSource(${docNumber})">[${docNumber}]</span>`;
                    })
                    .replace(/Document (\d+)/g, (match, docNumber) => {
                        return `<span class="citation" onclick="highlightSource(${docNumber})">Document ${docNumber}</span>`;
                    });
            }

            async function search() {
                const searchInput = document.getElementById('searchInput');
                const loading = document.getElementById('loading');
                const resultsDiv = document.getElementById('results');
                
                if (!searchInput.value.trim()) return;
                
                loading.style.display = 'block';
                resultsDiv.innerHTML = '';
                
                try {
                    const response = await fetch('/rag', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'Accept': 'application/json'
                        },
                        body: JSON.stringify({
                            query: searchInput.value.trim(),
                            n_results: 10,
                            temperature: 0.7
                        })
                    });
                    
                    if (!response.ok) {
                        const errorData = await response.json();
                        throw new Error(errorData.detail || 'Query failed');
                    }
                    
                    const result = await response.json();
                    
                    // Update the answer HTML to process citations
                    const answerHtml = `
                        <div class="result-section">
                            <h2 class="section-title">Final Answer</h2>
                            <div class="final-answer">${processCitations(escapeHtml(result.answer)).replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')}</div>
                        </div>
                    `;
                    
                    // Update the thinking process HTML to process citations
                    const thinkingHtml = `
                        <div class="result-section">
                            <h2 class="section-title">Thinking Process</h2>
                            <div class="thinking-process">${processCitations(escapeHtml(result.thinking_process))}</div>
                        </div>
                    `;
                    
                    // Update source documents HTML to include data attributes
                    let sourcesHtml = '<div class="result-section"><h2 class="section-title">Source Documents</h2>';
                    result.sources.forEach((source, index) => {
                        sourcesHtml += `
                            <div class="source-document" data-doc-number="${index + 1}">
                                <div class="source-header">Source: ${escapeHtml(source.source)}</div>
                                <div class="source-url">URL: ${escapeHtml(source.name)}</div>
                                <div>${escapeHtml(source.content)}</div>
                                <div class="similarity-score">Similarity: ${(source.similarity * 100).toFixed(1)}%</div>
                            </div>
                        `;
                    });
                    sourcesHtml += '</div>';
                    
                    // Combine all sections in the new order
                    resultsDiv.innerHTML = answerHtml + thinkingHtml + sourcesHtml;
                    
                } catch (error) {
                    resultsDiv.innerHTML = `
                        <div class="result-section" style="color: #e74c3c;">
                            <h2 class="section-title">Error</h2>
                            <p>${error.message}</p>
                        </div>
                    `;
                } finally {
                    loading.style.display = 'none';
                }
            }
            </script>
        </body>
    </html>
    """


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
