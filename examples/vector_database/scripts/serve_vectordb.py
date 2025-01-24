import argparse
import base64
import logging
from typing import List, Optional

import chromadb
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.responses import HTMLResponse
import numpy as np
import open_clip
from pydantic import BaseModel
import torch

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(title="Vector Database Search API")

# Global variables for model and database
model = None
tokenizer = None
collection = None
device = None


class SearchQuery(BaseModel):
    text: str
    n_results: Optional[int] = 5


class SearchResult(BaseModel):
    image_base64: str
    similarity: float


def encode_text(text: str, model_name: str = "ViT-bigG-14") -> np.ndarray:
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
                               include=["metadatas", "distances"])

    # Get images and distances
    images = [item['image_base64'] for item in results['metadatas'][0]]
    distances = results['distances'][0]

    # Convert distances to similarities (cosine similarity = 1 - distance/2)
    similarities = [1 - (d / 2) for d in distances]

    return [
        SearchResult(image_base64=img, similarity=similarity)
        for img, similarity in zip(images, similarities)
    ]


@app.post("/search", response_model=List[SearchResult])
async def search(query: SearchQuery):
    """Search endpoint that takes a text query and returns similar images."""
    try:
        # Encode the query text
        query_embedding = encode_text(query.text)

        # Query the collection
        results = query_collection(query_embedding, query.n_results)

        return results
    except Exception as e:
        logger.error(f"Error processing query: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "collection_size": collection.count() if collection else 0
    }


@app.get("/", response_class=HTMLResponse)
async def get_search_page():
    """Serve a simple search interface."""
    return """
    <html>
        <head>
            <title>Image Search</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 20px; }
                .search-container { margin-bottom: 20px; }
                .results { display: flex; flex-wrap: wrap; gap: 20px; }
                .result { 
                    border: 1px solid #ddd;
                    padding: 10px;
                    text-align: center;
                }
                img { max-width: 200px; max-height: 200px; }
            </style>
        </head>
        <body>
            <div class="search-container">
                <h1>SkyPilot Image Search</h1>
                <input type="text" id="searchInput" placeholder="Enter search text">
                <button onclick="search()">Search</button>
            </div>
            <div id="results" class="results"></div>
            
            <script>
            async function search() {
                const query = document.getElementById('searchInput').value;
                const response = await fetch('/search', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({text: query, n_results: 10})
                });
                const results = await response.json();
                
                const resultsDiv = document.getElementById('results');
                resultsDiv.innerHTML = results.map(result => `
                    <div class="result">
                        <img src="data:image/jpeg;base64,${result.image_base64}">
                        <p>Similarity: ${result.similarity.toFixed(3)}</p>
                    </div>
                `).join('');
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
    parser.add_argument('--model-name',
                        type=str,
                        default='ViT-bigG-14',
                        help='CLIP model name')

    args = parser.parse_args()

    # Initialize global variables
    global model, tokenizer, collection, device

    # Set device
    device = "cuda" if torch.cuda.is_available() else "cpu"
    logger.info(f"Using device: {device}")

    # Load the model
    import open_clip
    model, _, _ = open_clip.create_model_and_transforms(
        args.model_name, pretrained="laion2b_s39b_b160k", device=device)
    tokenizer = open_clip.get_tokenizer(args.model_name)

    # Initialize ChromaDB client
    client = chromadb.PersistentClient(path=args.persist_dir)

    try:
        # Get the collection
        collection = client.get_collection(name=args.collection_name)
        logger.info(f"Connected to collection: {args.collection_name}")
        logger.info(f"Total documents in collection: {collection.count()}")
    except ValueError as e:
        logger.error(f"Error: {str(e)}")
        logger.error(
            "Make sure the collection exists and the persist_dir is correct.")
        raise

    # Start the server
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
