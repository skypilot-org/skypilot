"""
Script to compute embeddings for Pile of Law dataset using DeepSeek R1 through vLLM.
"""

import argparse
import logging
import os
from pathlib import Path
import pickle
import time
from typing import List, Dict

from datasets import load_dataset
import numpy as np
import pandas as pd
import requests
from tqdm import tqdm

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def load_law_documents(start_idx: int = 0, end_idx: int = 1000):
    """Load documents from Pile of Law dataset."""
    dataset = load_dataset('pile-of-law/pile-of-law', split='train', streaming=True)
    
    documents = []
    for idx, doc in enumerate(dataset.skip(start_idx).take(end_idx - start_idx)):
        documents.append({
            'id': f"{doc['split']}/{doc['id']}",
            'name': doc['name'],
            'text': doc['text'],
            'split': doc['split'],
            'source': doc['source']
        })
        
        if (idx + 1) % 100 == 0:
            logger.info(f'Loaded {idx + 1} documents')
    
    return documents

def chunk_document(document, chunk_size=512, overlap=50):
    """Split document into overlapping chunks."""
    text = document['text']
    chunks = []
    
    # Split into paragraphs first
    paragraphs = text.split('\n\n')
    
    current_chunk = ''
    for para in paragraphs:
        # If adding this paragraph would exceed chunk size, save current chunk
        if len(current_chunk) + len(para) > chunk_size and current_chunk:
            chunks.append({
                'id': document['id'],
                'name': document['name'],
                'content': current_chunk.strip(),
                'split': document['split'],
                'source': document['source']
            })
            # Keep last part for overlap
            current_chunk = current_chunk[-overlap:] if overlap > 0 else ''
        
        current_chunk += '\n\n' + para
    
    # Add the last chunk if it's not empty
    if current_chunk.strip():
        chunks.append({
            'id': document['id'],
            'name': document['name'],
            'content': current_chunk.strip(),
            'split': document['split'],
            'source': document['source']
        })
    
    return chunks

def compute_embeddings_batch(chunks: List[Dict], vllm_endpoint: str, batch_size: int = 32) -> List[Dict]:
    """Compute embeddings for document chunks using DeepSeek R1."""
    all_embeddings = []
    
    # Process in batches
    for i in tqdm(range(0, len(chunks), batch_size), desc='Computing embeddings'):
        batch = chunks[i:i + batch_size]
        
        # Create prompt for each chunk
        prompts = [
            f"Please analyze this legal text and provide a comprehensive embedding. Text: {chunk['content']}"
            for chunk in batch
        ]
        
        try:
            # Get embeddings from vLLM
            response = requests.post(
                f"{vllm_endpoint}/v1/embeddings",
                json={
                    "input": prompts,
                    "model": "deepseek-ai/deepseek-r1-distill-llama-8b"
                },
                timeout=60
            )
            response.raise_for_status()
            
            # Extract embeddings
            embeddings = [data['embedding'] for data in response.json()['data']]
            
            # Combine embeddings with metadata
            for chunk, embedding in zip(batch, embeddings):
                all_embeddings.append({
                    'id': chunk['id'],
                    'name': chunk['name'],
                    'content': chunk['content'],
                    'split': chunk['split'],
                    'source': chunk['source'],
                    'embedding': pickle.dumps(np.array(embedding))  # Serialize the embedding
                })
                
        except Exception as e:
            logger.error(f"Error computing embeddings for batch: {str(e)}")
            # Wait a bit before retrying
            time.sleep(5)
            continue
    
    return all_embeddings

def main():
    parser = argparse.ArgumentParser(
        description='Compute embeddings for Pile of Law dataset')
    parser.add_argument('--output-path',
                       type=str,
                       required=True,
                       help='Path to save embeddings parquet file')
    parser.add_argument('--start-idx',
                       type=int,
                       default=0,
                       help='Starting index in dataset')
    parser.add_argument('--end-idx',
                       type=int,
                       default=1000,
                       help='Ending index in dataset')
    parser.add_argument('--chunk-size',
                       type=int,
                       default=512,
                       help='Size of document chunks')
    parser.add_argument('--chunk-overlap',
                       type=int,
                       default=50,
                       help='Overlap between chunks')
    parser.add_argument('--vllm-endpoint',
                       type=str,
                       required=True,
                       help='Endpoint for vLLM service')
    parser.add_argument('--batch-size',
                       type=int,
                       default=32,
                       help='Batch size for computing embeddings')
    
    args = parser.parse_args()
    
    # Create output directory if it doesn't exist
    os.makedirs(os.path.dirname(args.output_path), exist_ok=True)
    
    # Load documents
    logger.info('Loading documents from Pile of Law dataset...')
    documents = load_law_documents(args.start_idx, args.end_idx)
    logger.info(f'Loaded {len(documents)} documents')
    
    # Chunk documents
    logger.info('Chunking documents...')
    chunks = []
    for doc in documents:
        chunks.extend(chunk_document(doc, args.chunk_size, args.chunk_overlap))
    logger.info(f'Created {len(chunks)} chunks')
    
    # Compute embeddings
    logger.info('Computing embeddings...')
    embeddings = compute_embeddings_batch(chunks, args.vllm_endpoint, args.batch_size)
    
    # Save to parquet
    logger.info('Saving embeddings...')
    df = pd.DataFrame(embeddings)
    df.to_parquet(args.output_path)
    logger.info(f'Saved embeddings to {args.output_path}')

if __name__ == '__main__':
    main()
