"""
Script to compute embeddings for Pile of Law dataset using `Alibaba-NLP/gte-Qwen2-7B-instruct` through vLLM.
"""

import argparse
import logging
import os
from pathlib import Path
import pickle
import shutil
import time
from typing import Dict, List, Tuple

from datasets import load_dataset
import nltk
import numpy as np
import pandas as pd
import requests
from tqdm import tqdm

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize NLTK to chunk documents
try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    logger.info('Downloading NLTK punkt tokenizer...')
    nltk.download('punkt')
    nltk.download('punkt_tab')
    logger.info('Download complete')


def load_law_documents(start_idx: int = 0, end_idx: int = 1000) -> List[Dict]:
    """Load documents from Pile of Law dataset.
    
    Args:
        start_idx: Starting index in dataset
        end_idx: Ending index in dataset
        
    Returns:
        List of documents
    """
    dataset = load_dataset('pile-of-law/pile-of-law',
                           'all',
                           split='train',
                           streaming=True,
                           trust_remote_code=True)

    documents = []
    for idx, doc in enumerate(
            dataset.skip(start_idx).take(end_idx - start_idx)):
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


def chunk_document(document,
                   chunk_size=512,
                   overlap=50,
                   start_chunk_idx=0) -> Tuple[List[Dict], int]:
    """Split document into overlapping chunks using sentence-aware splitting.
    
    Args:
        document: The document to chunk
        chunk_size: Maximum size of each chunk in characters
        overlap: Number of characters to overlap between chunks
        start_chunk_idx: Starting index for global chunk counting
        
    Returns:
        List of chunks and the next available chunk index
    """
    text = document['text']
    chunks = []
    chunk_idx = start_chunk_idx

    # Split into sentences first
    sentences = nltk.sent_tokenize(text)

    current_chunk = []
    current_length = 0

    for sentence in sentences:
        sentence_len = len(sentence)

        # If adding this sentence would exceed chunk size, save current chunk
        if current_length + sentence_len > chunk_size and current_chunk:
            chunk_text = ' '.join(current_chunk)
            chunks.append({
                'id': document['id'] + '_' + str(chunk_idx),
                'name': document['name'],
                'content': document['text'],  # Store full document content
                'chunk_text': chunk_text.strip(),  # Store the specific chunk
                'chunk_start': len(' '.join(
                    current_chunk[:-(2 if overlap > 0 else 0)])) if overlap > 0
                               else 0,  # Approximate start position
                'split': document['split'],
                'source': document['source'],
                'document_id': document['id'],
                'document_url': document['url'],
                'document_created_timestamp': document['created_timestamp'],
                'document_downloaded_timestamp':
                    document['downloaded_timestamp']
            })
            chunk_idx += 1

            # Keep last few sentences for overlap
            overlap_text = ' '.join(current_chunk[-2:])  # Keep last 2 sentences
            current_chunk = [overlap_text] if overlap > 0 else []
            current_length = len(overlap_text) if overlap > 0 else 0

        current_chunk.append(sentence)
        current_length += sentence_len + 1  # +1 for space

    # Add the last chunk if it's not empty
    if current_chunk:
        chunk_text = ' '.join(current_chunk)
        chunks.append({
            'id': document['id'] + '_' + str(chunk_idx),
            'name': document['name'],
            'content': document['text'],  # Store full document content
            'chunk_text': chunk_text.strip(),  # Store the specific chunk
            'chunk_start': len(' '.join(
                current_chunk[:-(2 if overlap > 0 else 0)]))
                           if overlap > 0 else 0,  # Approximate start position
            'split': document['split'],
            'source': document['source'],
            'document_id': document['id'],
            'document_url': document['url'],
            'document_created_timestamp': document['created_timestamp'],
            'document_downloaded_timestamp': document['downloaded_timestamp']
        })
        chunk_idx += 1

    return chunks, chunk_idx


def compute_embeddings_batch(chunks: List[Dict],
                             vllm_endpoint: str,
                             output_path: str,
                             batch_size: int = 32,
                             partition_size: int = 1000) -> None:
    """Compute embeddings for document chunks using DeepSeek R1 and save in partitions.
    
    Args:
        chunks: List of document chunks
        vllm_endpoint: Endpoint for vLLM service
        output_path: Path to save embeddings
    """
    current_partition = []
    partition_counter = 0

    # Process in batches
    for i in tqdm(range(0, len(chunks), batch_size),
                  desc='Computing embeddings'):
        batch = chunks[i:i + batch_size]

        # Create prompt for each chunk - simplified prompt
        prompts = [chunk['chunk_text'] for chunk in batch]

        try:
            # Print request payload for debugging
            request_payload = {
                "model": "/tmp/model",
                # because this is loaded from the mounted directory
                "input": prompts
            }

            response = requests.post(f"{vllm_endpoint}/v1/embeddings",
                                     json=request_payload,
                                     timeout=60)

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
                    'chunk_text': chunk['chunk_text'],
                    'chunk_start': chunk['chunk_start'],
                    'split': chunk['split'],
                    'source': chunk['source'],
                    'embedding': pickle.dumps(np.array(embedding)),
                    # Include document metadata
                    'document_id': chunk['document_id'],
                    'document_url': chunk['document_url'],
                    'document_created_timestamp':
                        chunk['document_created_timestamp'],
                    'document_downloaded_timestamp':
                        chunk['document_downloaded_timestamp']
                })

            # Save partition when it reaches the desired size
            if len(current_partition) >= partition_size:
                save_partition(current_partition, output_path,
                               partition_counter)
                partition_counter += 1
                current_partition = []

        except Exception as e:
            logger.error(f"Error computing embeddings for batch: {str(e)}")
            time.sleep(5)
            continue

    # Save any remaining embeddings in the final partition
    if current_partition:
        save_partition(current_partition, output_path, partition_counter)


def save_partition(results: List[Dict], output_path: str,
                   partition_counter: int) -> None:
    """Save a partition of embeddings to a parquet file with atomic write.
    
    Args:
        results: List of embeddings
        output_path: Path to save embeddings
        partition_counter: Partition counter
    """
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

    logger.info(
        f'Saved partition {partition_counter} to {final_path} with {len(df)} rows'
    )


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
        doc_chunks, next_chunk_idx = chunk_document(doc, args.chunk_size,
                                                    args.chunk_overlap,
                                                    next_chunk_idx)
        chunks.extend(doc_chunks)
    logger.info(f'Created {len(chunks)} chunks')

    # Compute embeddings and save in partitions
    logger.info('Computing embeddings...')
    compute_embeddings_batch(chunks, args.vllm_endpoint, args.output_path,
                             args.batch_size, args.partition_size)
    logger.info('Finished computing and saving embeddings')


if __name__ == '__main__':
    main()
