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
import shutil

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
    dataset = load_dataset('pile-of-law/pile-of-law', 'all', split='train', streaming=True, trust_remote_code=True)
    
    documents = []
    for idx, doc in enumerate(dataset.skip(start_idx).take(end_idx - start_idx)):
        documents.append({
            'id': f"{idx + start_idx}",
            'name': doc['url'],
            'text': doc['text'],
            'split': 'train',
            'source': 'r_legaladvice',
            'created_timestamp': doc['created_timestamp'],
            'downloaded_timestamp': doc['downloaded_timestamp'],
            'url': doc['url']
        })
        
        if (idx + 1) % 100 == 0:
            logger.info(f'Loaded {idx + 1} documents')
    
    return documents

def chunk_document(document, chunk_size=512, overlap=50, start_chunk_idx=0):
    """Split document into overlapping chunks.
    
    Args:
        document: The document to chunk
        chunk_size: Maximum size of each chunk
        overlap: Number of characters to overlap between chunks
        start_chunk_idx: Starting index for global chunk counting
        
    Returns:
        List of chunks and the next available chunk index
    """
    text = document['text']
    chunks = []
    
    # Split into paragraphs first
    paragraphs = text.split('\n\n')
    
    current_chunk = ''
    chunk_idx = start_chunk_idx  # Use the provided starting index
    for para in paragraphs:
        # If adding this paragraph would exceed chunk size, save current chunk
        if len(current_chunk) + len(para) > chunk_size and current_chunk:
            chunks.append({
                'id': str(chunk_idx),  # Use global index as ID
                'name': document['name'],
                'content': current_chunk.strip(),
                'split': document['split'],
                'source': document['source']
            })
            chunk_idx += 1  # Increment global index
            # Keep last part for overlap
            current_chunk = current_chunk[-overlap:] if overlap > 0 else ''
        
        current_chunk += '\n\n' + para
    
    # Add the last chunk if it's not empty
    if current_chunk.strip():
        chunks.append({
            'id': str(chunk_idx),  # Use global index as ID
            'name': document['name'],
            'content': current_chunk.strip(),
            'split': document['split'],
            'source': document['source']
        })
        chunk_idx += 1
    
    return chunks, chunk_idx  # Return both chunks and next available index

def compute_embeddings_batch(chunks: List[Dict], vllm_endpoint: str,output_path: str, batch_size: int = 32, 
                            partition_size: int = 1000) -> None:
    """Compute embeddings for document chunks using DeepSeek R1 and save in partitions."""
    current_partition = []
    partition_counter = 0
    
    # Process in batches
    for i in tqdm(range(0, len(chunks), batch_size), desc='Computing embeddings'):
        batch = chunks[i:i + batch_size]
        
        # Create prompt for each chunk - simplified prompt
        prompts = [chunk['content'] for chunk in batch]
        
        try:
            # Print request payload for debugging
            request_payload = {
                "model": "deepseek-ai/DeepSeek-R1-Distill-Llama-8B",
                "input": prompts
            }
            
            response = requests.post(
                f"{vllm_endpoint}/v1/embeddings",
                json=request_payload,
                timeout=60
            )
            
            response.raise_for_status()
            
            # Extract embeddings - updated response parsing
            result = response.json()

            if 'data' not in result:
                raise ValueError(f"Unexpected response format: {result}")
            
            embeddings = [item['embedding'] for item in result['data']]
            
            # Combine embeddings with metadata
            for chunk, embedding in zip(batch, embeddings):
                current_partition.append({
                    'id': chunk['id'],
                    'name': chunk['name'],
                    'content': chunk['content'],
                    'split': chunk['split'],
                    'source': chunk['source'],
                    'embedding': pickle.dumps(np.array(embedding))
                })
            
            # Save partition when it reaches the desired size
            if len(current_partition) >= partition_size:
                save_partition(current_partition, output_path, partition_counter)
                partition_counter += 1
                current_partition = []
                
        except Exception as e:
            logger.error(f"Error computing embeddings for batch: {str(e)}")
            time.sleep(5)
            continue
    
    # Save any remaining embeddings in the final partition
    if current_partition:
        save_partition(current_partition, output_path, partition_counter)

def save_partition(results: List[Dict], output_path: str, partition_counter: int):
    """Save a partition of embeddings to a parquet file with atomic write."""
    if not results:
        return
        
    df = pd.DataFrame(results)
    final_path = f'{output_path}_part_{partition_counter}.parquet'
    temp_path = f'/tmp/embeddings_{partition_counter}.tmp'
    
    # Write to temporary file first
    df.to_parquet(temp_path, engine='pyarrow', index=False)
    
    # Copy from temp to final destination
    os.makedirs(os.path.dirname(final_path), exist_ok=True)
    shutil.copy2(temp_path, final_path)
    os.remove(temp_path)  # Clean up temp file
    
    logger.info(f'Saved partition {partition_counter} to {final_path} with {len(df)} rows')

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
    parser.add_argument('--partition-size',
                       type=int,
                       default=1000,
                       help='Number of embeddings per partition file')
    
    args = parser.parse_args()
    
    # Create output directory if it doesn't exist
    os.makedirs(os.path.dirname(args.output_path), exist_ok=True)
    
    # Load documents
    logger.info('Loading documents from Pile of Law dataset...')
    documents = load_law_documents(args.start_idx, args.end_idx)
    logger.info(f'Loaded {len(documents)} documents')
    
    # Chunk documents with global counter
    logger.info('Chunking documents...')
    chunks = []
    next_chunk_idx = 0  # Initialize global chunk counter
    for doc in documents:
        doc_chunks, next_chunk_idx = chunk_document(doc, args.chunk_size, args.chunk_overlap, next_chunk_idx)
        chunks.extend(doc_chunks)
    logger.info(f'Created {len(chunks)} chunks')
    
    # Compute embeddings and save in partitions
    logger.info('Computing embeddings...')
    compute_embeddings_batch(chunks, args.vllm_endpoint, args.output_path, 
                           args.batch_size, args.partition_size)
    logger.info('Finished computing and saving embeddings')

if __name__ == '__main__':
    main()
