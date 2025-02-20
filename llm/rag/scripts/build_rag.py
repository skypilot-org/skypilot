"""
This script is responsible for building the vector database from the mounted bucket and saving
it to another mounted bucket.
"""

import argparse
import base64
from concurrent.futures import as_completed
from concurrent.futures import ProcessPoolExecutor
import glob
import logging
import multiprocessing
import os
import pickle
import shutil
import tempfile

import chromadb
import numpy as np
import pandas as pd
from tqdm import tqdm

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def list_local_parquet_files(mount_path: str, prefix: str) -> list:
    """List all parquet files in the mounted directory."""
    search_path = os.path.join(mount_path, prefix, '**/*.parquet')
    parquet_files = glob.glob(search_path, recursive=True)
    return parquet_files


def process_parquet_file(args):
    """Process a single parquet file and return the processed data."""
    parquet_file, batch_size = args
    try:
        results = []
        df = pd.read_parquet(parquet_file)

        # Process in batches
        for i in range(0, len(df), batch_size):
            batch_df = df.iloc[i:i + batch_size]
            # Extract data from DataFrame
            ids = [str(idx) for idx in batch_df['id']]
            embeddings = [pickle.loads(emb) for emb in batch_df['embedding']]
            documents = batch_df['content'].tolist(
            )  # Content goes to documents
            # Create metadata from the available fields (excluding content)
            metadatas = [{
                'name': row['name'],
                'split': row['split'],
                'source': row['source'],
            } for _, row in batch_df.iterrows()]
            results.append((ids, embeddings, documents, metadatas))

        return results
    except Exception as e:
        logger.error(f'Error processing file {parquet_file}: {str(e)}')
        return None


def main():
    parser = argparse.ArgumentParser(
        description='Build ChromaDB from mounted parquet files')
    parser.add_argument('--collection-name',
                        type=str,
                        default='rag_embeddings',
                        help='ChromaDB collection name')
    parser.add_argument('--persist-dir',
                        type=str,
                        default='/vectordb/chroma',
                        help='Directory to persist ChromaDB')
    parser.add_argument(
        '--batch-size',
        type=int,
        default=1000,
        help='Batch size for processing, this needs to fit in memory')
    parser.add_argument('--embeddings-dir',
                        type=str,
                        default='/embeddings',
                        help='Path to mounted bucket containing parquet files')
    parser.add_argument(
        '--prefix',
        type=str,
        default='',
        help='Prefix path within mounted bucket to search for parquet files')

    args = parser.parse_args()

    # Create a temporary directory for building the database. The
    # mounted bucket does not support append operation, so build in
    # the tmpdir and then copy it to the final location.
    with tempfile.TemporaryDirectory() as temp_dir:
        logger.info(f'Using temporary directory: {temp_dir}')

        # Initialize ChromaDB in temporary directory
        client = chromadb.PersistentClient(path=temp_dir)

        # Create or get collection for chromadb
        # it attempts to create a collection with the same name
        # if it already exists, it will get the collection
        try:
            collection = client.create_collection(
                name=args.collection_name,
                metadata={'description': 'RAG embeddings from legal documents'})
            logger.info(f'Created new collection: {args.collection_name}')
        except ValueError:
            collection = client.get_collection(name=args.collection_name)
            logger.info(f'Using existing collection: {args.collection_name}')

        # List parquet files from mounted directory
        parquet_files = list_local_parquet_files(args.embeddings_dir,
                                                 args.prefix)
        logger.info(f'Found {len(parquet_files)} parquet files')

        # Process files in parallel
        max_workers = max(1,
                          multiprocessing.cpu_count() - 1)  # Leave one CPU free
        logger.info(f'Processing files using {max_workers} workers')

        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            # Submit all files for processing
            future_to_file = {
                executor.submit(process_parquet_file, (file, args.batch_size)):
                file for file in parquet_files
            }

            # Process results as they complete
            for future in tqdm(as_completed(future_to_file),
                               total=len(parquet_files),
                               desc='Processing files'):
                file = future_to_file[future]
                try:
                    results = future.result()
                    if results:
                        for ids, embeddings, documents, metadatas in results:
                            collection.add(ids=list(ids),
                                           embeddings=list(embeddings),
                                           documents=list(documents),
                                           metadatas=list(metadatas))
                except Exception as e:
                    logger.error(f'Error processing file {file}: {str(e)}')
                    continue

        logger.info('Vector database build complete!')
        logger.info(f'Total documents in collection: {collection.count()}')

        # Copy the completed database to the final location
        logger.info(f'Copying database to final location: {args.persist_dir}')
        if os.path.exists(args.persist_dir):
            logger.info('Removing existing database directory')
            shutil.rmtree(args.persist_dir)
        shutil.copytree(temp_dir, args.persist_dir)
        logger.info('Database copy complete!')


if __name__ == '__main__':
    main()
