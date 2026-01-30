import asyncio
import json
import logging
import os
from pathlib import Path
import pickle
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from base_vector_processor import BaseVectorProcessor
import nltk
import numpy as np
import pandas as pd
import requests
from tqdm import tqdm

# Initialize NLTK for text chunking
try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    logging.info('Downloading NLTK punkt tokenizer...')
    nltk.download('punkt')
    nltk.download('punkt_tab')
    logging.info('Download complete')


class TextVectorProcessor(BaseVectorProcessor):
    """Process text data to compute vector embeddings."""

    def __init__(self,
                 output_path: str,
                 vllm_endpoint: str,
                 model_name: str = "Alibaba-NLP/gte-Qwen2-7B-instruct",
                 dataset_name: str = 'pile-of-law/pile-of-law',
                 dataset_config: str = 'all',
                 split: str = 'full',
                 streaming: bool = True,
                 batch_size: int = 32,
                 checkpoint_size: int = 100,
                 start_idx: int = 0,
                 end_idx: Optional[int] = None,
                 chunk_size: int = 512,
                 chunk_overlap: int = 50,
                 max_preprocessing_tasks: int = 10,
                 partition_method: str = 'chunk',
                 worker_rank: int = 0,
                 total_workers: int = 1,
                 global_start_idx: int = 0,
                 global_end_idx: Optional[int] = None):
        """Initialize the text vector processor.
        
        Args:
            output_path: Path to save the computed vectors
            vllm_endpoint: Endpoint for vLLM service
            model_name: Name of the model to use for embeddings
            dataset_name: Name of the dataset to process
            dataset_config: Dataset configuration
            split: Dataset split to use
            streaming: Whether to stream the dataset
            batch_size: Size of batches for processing
            checkpoint_size: Number of items to process before saving
            start_idx: Starting index in the dataset
            end_idx: Ending index in the dataset
            chunk_size: Size of document chunks
            chunk_overlap: Overlap between chunks
            max_preprocessing_tasks: Maximum number of concurrent preprocessing tasks
            partition_method: Method of partitioning ('chunk' or 'stride')
            worker_rank: Rank of this worker (0-based)
            total_workers: Total number of workers
            global_start_idx: Global starting index for all workers
            global_end_idx: Global ending index for all workers
        """
        # If using stride method, adjust start_idx and end_idx
        self.partition_method = partition_method
        self.worker_rank = worker_rank
        self.total_workers = total_workers
        self.global_start_idx = global_start_idx
        self.global_end_idx = global_end_idx

        # For stride method, we'll handle the actual skipping in get_dataset_iterator
        # but we keep the original range for BaseVectorProcessor

        super().__init__(output_path=output_path,
                         dataset_name=dataset_name,
                         split=split,
                         streaming=streaming,
                         batch_size=batch_size,
                         checkpoint_size=checkpoint_size,
                         start_idx=start_idx,
                         end_idx=end_idx,
                         max_preprocessing_tasks=max_preprocessing_tasks)

        # Text-specific attributes
        self.vllm_endpoint = vllm_endpoint
        self.model_name = model_name
        self.dataset_config = dataset_config
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.next_chunk_idx = 0  # Global chunk counter
        self.chunks = []  # Store preprocessed chunks

        # Token tracking attributes
        self.total_tokens = 0
        self.batch_count = 0
        self.token_metrics = {
            'total_tokens': 0,
            'avg_tokens_per_batch': 0,
            'avg_tokens_per_chunk': 0,
            'token_count_by_batch': {}
        }

        # Log partitioning method
        if self.partition_method == 'stride':
            logging.info(
                f"Using strided partitioning: worker {self.worker_rank} of {self.total_workers}, "
                f"processing every {self.total_workers}th item starting from {self.global_start_idx}"
            )
        else:
            logging.info(
                f"Using chunk partitioning: processing items from {self.start_idx} to {self.end_idx}"
            )

    async def setup_model(self):
        """Verify vLLM endpoint is accessible."""
        try:
            response = requests.get(f"{self.vllm_endpoint}/health", timeout=10)
            if response.status_code == 200:
                logging.info(
                    f"Successfully connected to vLLM endpoint: {self.vllm_endpoint}"
                )
                self.model = True  # Just a flag to indicate setup is complete
            else:
                raise ConnectionError(
                    f"vLLM endpoint returned status code: {response.status_code}"
                )
        except Exception as e:
            logging.error(f"Failed to connect to vLLM endpoint: {str(e)}")
            raise

    async def get_dataset_iterator(self) -> AsyncIterator[Tuple[int, Any]]:
        """Load data from a HuggingFace dataset."""
        from datasets import load_dataset

        dataset = load_dataset(self.dataset_name,
                               self.dataset_config,
                               split=self.split,
                               streaming=self.streaming,
                               trust_remote_code=True)

        # Handle different partitioning methods
        if self.partition_method == 'stride':
            # For stride method, we process every Nth item where N is total_workers
            # starting from global_start_idx + worker_rank
            start_point = self.global_start_idx + self.worker_rank

            item_counter = 0
            for idx, item in enumerate(dataset, start=0):
                if idx < start_point:
                    continue

                # Only process items that belong to this worker based on the stride
                if (idx - start_point) % self.total_workers == 0:
                    # Check global end condition
                    if self.global_end_idx and idx >= self.global_end_idx:
                        break

                    # Transform item into a document format
                    document = {
                        'id': f"{idx}",
                        'name': item.get('url', f"document_{idx}"),
                        'text': item['text'],
                        'split': self.split,
                        'source': item.get('source', self.dataset_name),
                        'created_timestamp': item.get('created_timestamp',
                                                      None),
                        'downloaded_timestamp': item.get(
                            'downloaded_timestamp', None),
                        'url': item.get('url', None)
                    }

                    yield idx, document
                    item_counter += 1

                    # Provide some logging feedback
                    if item_counter % 100 == 0:
                        logging.info(
                            f"Worker {self.worker_rank}: Processed {item_counter} items (global idx {idx})"
                        )
        else:
            # Original chunk behavior
            if self.start_idx > 0:
                dataset = dataset.skip(self.start_idx)

            for idx, item in enumerate(dataset, start=self.start_idx):
                if self.end_idx and idx >= self.end_idx:
                    break

                # Transform item into a document format
                document = {
                    'id': f"{idx}",
                    'name': item.get('url', f"document_{idx}"),
                    'text': item['text'],
                    'split': self.split,
                    'source': item.get('source', self.dataset_name),
                    'created_timestamp': item.get('created_timestamp', None),
                    'downloaded_timestamp': item.get('downloaded_timestamp',
                                                     None),
                    'url': item.get('url', None)
                }

                yield idx, document

    async def _preprocess_input(self, document: Dict) -> Optional[List[Dict]]:
        """Chunk a document into smaller pieces."""
        try:
            doc_chunks = self.chunk_document(document)
            if doc_chunks:
                self.chunks.extend(doc_chunks)
                return doc_chunks
        except Exception as e:
            self.failed_count += 1
            self.processed_count += 1  # Count failed items as processed
            logging.debug(
                f"Error preprocessing document {document['id']}: {str(e)}")
        return None

    def chunk_document(self, document: Dict) -> List[Dict]:
        """Chunk a document into smaller pieces.
        
        Args:
            document: Document to chunk
            
        Returns:
            List of chunks
        """
        from nltk.tokenize import sent_tokenize

        doc_id = document['id']
        text = document['text']

        # Skip empty documents
        if not text or not text.strip():
            return []

        # Split text into sentences
        try:
            sentences = sent_tokenize(text)
        except Exception as e:
            logging.warning(f"Error tokenizing document {doc_id}: {str(e)}")
            # Fallback to simple splitting
            sentences = text.split('. ')

        # Initialize chunks
        chunks = []
        current_chunk = []
        current_size = 0

        # Track token positions for each sentence
        for sentence in sentences:
            # Skip empty sentences
            if not sentence.strip():
                continue

            sentence_tokens = sentence.split()
            sentence_size = len(sentence_tokens)

            # If sentence is too big for a single chunk, split it further
            if sentence_size > self.chunk_size:
                if current_chunk:
                    # Save current chunk before processing long sentence
                    chunk_id = f"{doc_id}_chunk_{self.next_chunk_idx}"
                    chunk_text = ' '.join(current_chunk)

                    chunks.append({
                        'id': chunk_id,
                        'name': document['name'],
                        'document_id': doc_id,
                        'chunk_text': chunk_text,
                        'content': chunk_text,  # For compatibility with embedding API
                        'chunk_start': current_size - len(current_chunk),
                        'split': document['split'],
                        'source': document['source'],
                        'document_url': document.get('url'),
                        'document_created_timestamp':
                            document.get('created_timestamp'),
                        'document_downloaded_timestamp':
                            document.get('downloaded_timestamp')
                    })
                    self.next_chunk_idx += 1
                    current_chunk = []

                # Split long sentence into multiple chunks
                for i in range(0, sentence_size, self.chunk_size):
                    sub_sentence = ' '.join(sentence_tokens[i:i +
                                                            self.chunk_size])
                    chunk_id = f"{doc_id}_chunk_{self.next_chunk_idx}"

                    chunks.append({
                        'id': chunk_id,
                        'name': document['name'],
                        'document_id': doc_id,
                        'chunk_text': sub_sentence,
                        'content': sub_sentence,
                        'chunk_start': current_size + i,
                        'split': document['split'],
                        'source': document['source'],
                        'document_url': document.get('url'),
                        'document_created_timestamp':
                            document.get('created_timestamp'),
                        'document_downloaded_timestamp':
                            document.get('downloaded_timestamp')
                    })
                    self.next_chunk_idx += 1

                current_size += sentence_size
                continue

            # If adding this sentence would exceed chunk size, start a new chunk
            if current_size + sentence_size > self.chunk_size and current_chunk:
                chunk_id = f"{doc_id}_chunk_{self.next_chunk_idx}"
                chunk_text = ' '.join(current_chunk)

                chunks.append({
                    'id': chunk_id,
                    'name': document['name'],
                    'document_id': doc_id,
                    'chunk_text': chunk_text,
                    'content': chunk_text,
                    'chunk_start': current_size - len(current_chunk),
                    'split': document['split'],
                    'source': document['source'],
                    'document_url': document.get('url'),
                    'document_created_timestamp':
                        document.get('created_timestamp'),
                    'document_downloaded_timestamp':
                        document.get('downloaded_timestamp')
                })
                self.next_chunk_idx += 1

                # Keep some overlap for context
                overlap_tokens = min(self.chunk_overlap, len(current_chunk))
                current_chunk = current_chunk[
                    -overlap_tokens:] if overlap_tokens > 0 else []

            # Add sentence to current chunk
            current_chunk.append(sentence)
            current_size += sentence_size

        # Don't forget the last chunk
        if current_chunk:
            chunk_id = f"{doc_id}_chunk_{self.next_chunk_idx}"
            chunk_text = ' '.join(current_chunk)

            chunks.append({
                'id': chunk_id,
                'name': document['name'],
                'document_id': doc_id,
                'chunk_text': chunk_text,
                'content': chunk_text,
                'chunk_start': current_size - len(current_chunk),
                'split': document['split'],
                'source': document['source'],
                'document_url': document.get('url'),
                'document_created_timestamp': document.get('created_timestamp'),
                'document_downloaded_timestamp':
                    document.get('downloaded_timestamp')
            })
            self.next_chunk_idx += 1

        return chunks

    async def do_batch_processing(
            self, batch: List[Tuple[int,
                                    List[Dict]]]) -> List[Tuple[int, Dict]]:
        """Process a batch of document chunks."""
        if self.model is None:
            await self.setup_model()

        # Flatten the chunks from all documents
        all_chunks = []
        for _, chunks in batch:
            if chunks:
                all_chunks.extend(chunks)

        if not all_chunks:
            return []

        results = []

        # Process in smaller batches to avoid API limits
        for i in range(0, len(all_chunks), self.batch_size):
            batch_chunks = all_chunks[i:i + self.batch_size]

            # Get text for each chunk
            prompts = [chunk['content'] for chunk in batch_chunks]

            try:
                # Create request payload
                request_payload = {
                    "model": self.model_name,
                    "input": prompts,
                    "encoding_format": "float"
                }

                # Send request to vLLM service
                response = requests.post(f"{self.vllm_endpoint}/v1/embeddings",
                                         json=request_payload,
                                         timeout=60)

                response.raise_for_status()

                # Extract embeddings
                result = response.json()

                if 'data' not in result:
                    raise ValueError(f"Unexpected response format: {result}")

                embeddings = [item['embedding'] for item in result['data']]

                # Extract token counts if available
                batch_token_count = 0
                if 'usage' in result:
                    batch_token_count = result['usage'].get('total_tokens', 0)
                    self.total_tokens += batch_token_count
                    self.batch_count += 1

                    # Update token metrics
                    self.token_metrics['total_tokens'] = self.total_tokens
                    self.token_metrics[
                        'avg_tokens_per_batch'] = self.total_tokens / self.batch_count
                    self.token_metrics[
                        'avg_tokens_per_chunk'] = self.total_tokens / (
                            self.batch_count * len(batch_chunks))
                    self.token_metrics['token_count_by_batch'][str(
                        self.batch_count)] = batch_token_count

                    # Log token usage
                    logging.info(
                        f"Batch {self.batch_count} token count: {batch_token_count}, "
                        f"Total tokens: {self.total_tokens}, "
                        f"Avg tokens per batch: {self.token_metrics['avg_tokens_per_batch']:.2f}"
                    )

                # Combine embeddings with metadata
                for chunk, embedding in zip(batch_chunks, embeddings):
                    # Find the document index this chunk belongs to
                    doc_id = chunk['document_id']
                    chunk_id = chunk['id']

                    results.append((int(doc_id), {
                        'id': chunk_id,
                        'document_id': doc_id,
                        'name': chunk['name'],
                        'content': chunk['content'],
                        'chunk_text': chunk['chunk_text'],
                        'chunk_start': chunk['chunk_start'],
                        'split': chunk['split'],
                        'source': chunk['source'],
                        'embedding': np.array(embedding),
                        'document_url': chunk.get('document_url'),
                        'document_created_timestamp':
                            chunk.get('document_created_timestamp'),
                        'document_downloaded_timestamp':
                            chunk.get('document_downloaded_timestamp'),
                        'token_count': batch_token_count // len(batch_chunks)
                                       if batch_token_count > 0 else 0
                    }))
                    self.processed_count += 1

            except Exception as e:
                logging.error(f"Error computing embeddings for batch: {str(e)}")
                time.sleep(5)
                # We'll count these items as processed but failed
                for chunk in batch_chunks:
                    self.failed_count += 1
                    self.processed_count += 1

        return results

    def update_metrics(self):
        """Override update_metrics to include token statistics."""
        # Call the parent class method first
        super().update_metrics()

        # Add token metrics to the most recent metrics
        if self.metrics_history and self.total_tokens > 0:
            # Update the most recent metrics entry with token information
            self.metrics_history[-1].update({
                'total_tokens': self.total_tokens,
                'avg_tokens_per_batch':
                    self.token_metrics['avg_tokens_per_batch'],
                'avg_tokens_per_chunk':
                    self.token_metrics['avg_tokens_per_chunk'],
                'tokens_per_second':
                    self.total_tokens /
                    self.metrics_history[-1]['elapsed_seconds']
                    if self.metrics_history[-1]['elapsed_seconds'] > 0 else 0
            })

            # Save the updated metrics
            try:
                with open(self.metrics_file, 'w') as f:
                    json.dump(self.metrics_history[-1], f)
                self.save_metrics_history()
            except Exception as e:
                logging.warning(f"Failed to save metrics with token stats: {e}")

    def save_results_to_parquet(self, results: List[Tuple[int, Dict]]):
        """Save results to a parquet file with partition."""
        if not results:
            return

        # Extract results to DataFrames
        embeddings_data = []

        for _, item in results:
            embedding_bytes = pickle.dumps(item['embedding'])

            embeddings_data.append({
                'id': item['id'],
                'document_id': item['document_id'],
                'name': item['name'],
                'content': item['content'],
                'chunk_text': item['chunk_text'],
                'chunk_start': item['chunk_start'],
                'split': item['split'],
                'source': item['source'],
                'embedding': embedding_bytes,
                'document_url': item.get('document_url'),
                'document_created_timestamp':
                    item.get('document_created_timestamp'),
                'document_downloaded_timestamp':
                    item.get('document_downloaded_timestamp'),
                'token_count': item.get('token_count', 0)  # Include token count
            })

        # Create DataFrame
        df = pd.DataFrame(embeddings_data)

        # Create the output directory if it doesn't exist
        os.makedirs(self.output_path.parent, exist_ok=True)

        # Generate the partition output path
        base_name = self.output_path.stem
        output_path = self.output_path.parent / f"{base_name}_part{self.partition_counter}.parquet"

        # Save to parquet
        df.to_parquet(output_path)
        logging.info(f"Saved {len(df)} embeddings to {output_path}")
        logging.info(
            f"Token metrics: total={self.total_tokens}, avg_per_batch={self.token_metrics['avg_tokens_per_batch']:.2f}"
        )

        # Update metrics after saving results
        self.update_metrics()

        # Increment partition counter
        self.partition_counter += 1


async def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description='Compute text embeddings')
    parser.add_argument('--output-path',
                        type=str,
                        required=True,
                        help='Path to save the output parquet file')
    parser.add_argument('--start-idx',
                        type=int,
                        default=0,
                        help='Starting index in the dataset')
    parser.add_argument('--end-idx',
                        type=int,
                        default=1000,
                        help='Ending index in the dataset')
    parser.add_argument('--chunk-size',
                        type=int,
                        default=512,
                        help='Size of document chunks')
    parser.add_argument('--chunk-overlap',
                        type=int,
                        default=50,
                        help='Overlap between chunks')
    parser.add_argument('--batch-size',
                        type=int,
                        default=32,
                        help='Batch size for processing')
    parser.add_argument('--checkpoint-size',
                        type=int,
                        default=100,
                        help='Number of items to process before saving')
    parser.add_argument('--vllm-endpoint',
                        type=str,
                        required=True,
                        help='Endpoint for vLLM service')
    parser.add_argument('--model-name',
                        type=str,
                        default='Alibaba-NLP/gte-Qwen2-7B-instruct',
                        help='Model name')
    parser.add_argument('--dataset-name',
                        type=str,
                        default='pile-of-law/pile-of-law',
                        help='HuggingFace dataset name')
    parser.add_argument('--dataset-config',
                        type=str,
                        default='all',
                        help='Dataset configuration')
    parser.add_argument(
        '--partition-method',
        type=str,
        choices=['chunk', 'stride'],
        default=os.environ.get('PARTITION_METHOD', 'stride'),
        help=
        'Method to partition data: chunk (contiguous) or stride (interleaved)')
    parser.add_argument('--worker-rank',
                        type=int,
                        default=int(os.environ.get('WORKER_RANK', 0)),
                        help='Rank of this worker (0-based)')
    parser.add_argument('--total-workers',
                        type=int,
                        default=int(os.environ.get('TOTAL_WORKERS', 1)),
                        help='Total number of workers')
    parser.add_argument('--global-start-idx',
                        type=int,
                        default=int(os.environ.get('GLOBAL_START_IDX', 0)),
                        help='Global starting index for all workers')
    parser.add_argument('--global-end-idx',
                        type=int,
                        default=int(os.environ.get('GLOBAL_END_IDX', 0)) or
                        None,
                        help='Global ending index for all workers')
    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # Initialize processor
    processor = TextVectorProcessor(output_path=args.output_path,
                                    vllm_endpoint=args.vllm_endpoint,
                                    start_idx=args.start_idx,
                                    end_idx=args.end_idx,
                                    batch_size=args.batch_size,
                                    checkpoint_size=args.checkpoint_size,
                                    chunk_size=args.chunk_size,
                                    chunk_overlap=args.chunk_overlap,
                                    model_name=args.model_name,
                                    dataset_name=args.dataset_name,
                                    dataset_config=args.dataset_config,
                                    partition_method=args.partition_method,
                                    worker_rank=args.worker_rank,
                                    total_workers=args.total_workers,
                                    global_start_idx=args.global_start_idx,
                                    global_end_idx=args.global_end_idx)

    # Run processing
    await processor.run()


if __name__ == '__main__':
    asyncio.run(main())
