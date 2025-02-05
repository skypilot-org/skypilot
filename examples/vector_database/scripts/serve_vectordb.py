"""
This script is responsible for serving the vector database.
"""

import argparse
import base64
import logging
import os
from pathlib import Path
from typing import List, Optional

import chromadb
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import numpy as np
import open_clip
from pydantic import BaseModel
import torch

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(title='Vector Database Search API')

# Global variables for model and database
model = None
tokenizer = None
collection = None
device = None
images_dir = None


class SearchQuery(BaseModel):
    text: str
    n_results: Optional[int] = 5


class SearchResult(BaseModel):
    image_path: str
    similarity: float


def encode_text(text: str, model_name: str = 'ViT-bigG-14') -> np.ndarray:
    """Encode text using CLIP model."""
    global model, tokenizer, device

    # Tokenize and encode
    text_tokens = tokenizer([text]).to(device)

    with torch.no_grad():
        text_features = model.encode_text(text_tokens)
        # Normalize the features
        text_features /= text_features.norm(dim=-1, keepdim=True)

    return text_features.cpu().numpy()


def query_collection(query_embedding: np.ndarray,
                     n_results: int = 5) -> List[SearchResult]:
    """Query the collection and return top matches with scores."""
    global collection

    results = collection.query(query_embeddings=query_embedding.tolist(),
                               n_results=n_results,
                               include=['metadatas', 'distances', 'documents'])

    # Get image paths and distances
    image_paths = results['documents'][0]
    distances = results['distances'][0]

    # Convert distances to similarities (cosine similarity = 1 - distance/2)
    similarities = [1 - (d / 2) for d in distances]

    return [
        SearchResult(image_path=img_path, similarity=similarity)
        for img_path, similarity in zip(image_paths, similarities)
    ]


@app.post('/search', response_model=List[SearchResult])
async def search(query: SearchQuery):
    """Search endpoint that takes a text query and returns similar images."""
    try:
        # Encode the query text
        query_embedding = encode_text(query.text)

        # Query the collection
        results = query_collection(query_embedding, query.n_results)

        return results
    except Exception as e:
        logger.error(f'Error processing query: {str(e)}')
        raise HTTPException(status_code=500, detail=str(e))


@app.get('/image/{subpath:path}')
async def get_image(subpath: str):
    """Serve an image from the mounted bucket."""
    image_path = os.path.join(images_dir, subpath)
    if not os.path.exists(image_path):
        raise HTTPException(status_code=404, detail='Image not found')
    return FileResponse(image_path, media_type='image/jpeg')


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
            <title>Image Search</title>
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
                    max-width: 600px;
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
                .results {
                    display: grid;
                    grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
                    gap: 1.5rem;
                    padding: 1rem;
                }
                .result {
                    background: white;
                    border-radius: 10px;
                    overflow: hidden;
                    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
                    transition: transform 0.3s ease;
                }
                .result:hover {
                    transform: translateY(-5px);
                }
                .result img {
                    width: 100%;
                    height: 200px;
                    object-fit: cover;
                }
                .result-info {
                    padding: 1rem;
                }
                .similarity-score {
                    color: #2c3e50;
                    font-weight: 600;
                }
                #loading {
                    display: none;
                    text-align: center;
                    margin: 2rem 0;
                    font-size: 1.2rem;
                    color: #666;
                }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="search-container">
                    <h1>SkyPilot Image Search</h1>
                    <div class="search-box">
                        <input type="text" id="searchInput" placeholder="Enter your search query..."
                            onkeypress="if(event.key === 'Enter') search()">
                        <button onclick="search()">Search</button>
                    </div>
                </div>
                <div id="loading">Searching...</div>
                <div id="results" class="results"></div>
            </div>
            
            <script>
            async function search() {
                const searchInput = document.getElementById('searchInput');
                const loading = document.getElementById('loading');
                const resultsDiv = document.getElementById('results');
                
                if (!searchInput.value.trim()) return;
                
                loading.style.display = 'block';
                resultsDiv.innerHTML = '';
                
                try {
                    const response = await fetch('/search', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'Accept': 'application/json'
                        },
                        body: JSON.stringify({
                            text: searchInput.value.trim(),
                            n_results: 12
                        })
                    });
                    
                    if (!response.ok) {
                        const errorData = await response.json();
                        throw new Error(errorData.detail || 'Search failed');
                    }
                    
                    const results = await response.json();
                    resultsDiv.innerHTML = results.map(result => `
                        <div class="result">
                            <img src="/image/${result.image_path}"
                                alt="Search result">
                            <div class="result-info">
                                <p class="similarity-score">
                                    Similarity: ${(result.similarity * 100).toFixed(1)}%
                                </p>
                            </div>
                        </div>
                    `).join('');
                } catch (error) {
                    resultsDiv.innerHTML = `
                        <p style="color: #e74c3c; text-align: center; width: 100%;">
                            Error: ${error.message}
                        </p>
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
    parser = argparse.ArgumentParser(
        description='Serve Vector Database with FastAPI')
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
                        default='clip_embeddings',
                        help='ChromaDB collection name')
    parser.add_argument('--persist-dir',
                        type=str,
                        default='/vectordb/chroma',
                        help='Directory where ChromaDB is persisted')
    parser.add_argument('--images-dir',
                        type=str,
                        default='/images',
                        help='Directory where images are stored')
    parser.add_argument('--model-name',
                        type=str,
                        default='ViT-bigG-14',
                        help='CLIP model name')

    args = parser.parse_args()

    # Initialize global variables
    global model, tokenizer, collection, device, images_dir

    # Set device
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    logger.info(f'Using device: {device}')

    # Set images directory
    images_dir = args.images_dir

    # Load the model
    import open_clip
    model, _, _ = open_clip.create_model_and_transforms(
        args.model_name, pretrained='laion2b_s39b_b160k', device=device)
    tokenizer = open_clip.get_tokenizer(args.model_name)

    # Initialize ChromaDB client
    client = chromadb.PersistentClient(path=args.persist_dir)

    try:
        # Get the collection
        collection = client.get_collection(name=args.collection_name)
        logger.info(f'Connected to collection: {args.collection_name}')
        logger.info(f'Total documents in collection: {collection.count()}')
    except ValueError as e:
        logger.error(f'Error: {str(e)}')
        logger.error(
            'Make sure the collection exists and the persist_dir is correct.')
        raise

    # Start the server
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == '__main__':
    main()
