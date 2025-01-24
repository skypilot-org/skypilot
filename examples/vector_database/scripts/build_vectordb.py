import argparse
import base64
import glob
import logging
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
    """List all parquet files in the mounted S3 directory."""
    search_path = os.path.join(mount_path, prefix, "**/*.parquet")
    parquet_files = glob.glob(search_path, recursive=True)
    return parquet_files


def read_parquet_file(file_path: str) -> pd.DataFrame:
    """Read a parquet file into a pandas DataFrame."""
    return pd.read_parquet(file_path)


def process_batch(collection, batch_df):
    """Process a batch of data and add it to the ChromaDB collection."""
    # Extract data from DataFrame and unpack the pickled data
    ids = [str(idx) for idx in batch_df['idx']]

    # Unpack the pickled data to get images and embeddings
    unpacked_data = [pickle.loads(row) for row in batch_df['output']]
    # Each row now contains (image_base64, embedding)
    images_base64, embeddings = zip(*unpacked_data)

    embeddings = [embedding for embedding in embeddings]
    # Store both image data and embeddings in metadata
    metadatas = [{'image_base64': img} for img in images_base64]

    # Add to collection
    collection.add(ids=ids, embeddings=embeddings, metadatas=metadatas)


def main():
    parser = argparse.ArgumentParser(
        description='Build ChromaDB from mounted S3 parquet files')
    parser.add_argument('--collection-name',
                        type=str,
                        default='clip_embeddings',
                        help='ChromaDB collection name')
    parser.add_argument('--persist-dir',
                        type=str,
                        default='/vectordb/chroma',
                        help='Directory to persist ChromaDB')
    parser.add_argument('--batch-size',
                        type=int,
                        default=1000,
                        help='Batch size for processing')
    parser.add_argument('--embeddings-dir',
                        type=str,
                        default='/clip_embeddings',
                        help='Path to mounted bucket containing parquet files')
    parser.add_argument(
        '--prefix',
        type=str,
        default='',
        help='Prefix path within mounted bucket to search for parquet files')

    args = parser.parse_args()

    # Create a temporary directory for building the database
    with tempfile.TemporaryDirectory() as temp_dir:
        logger.info(f"Using temporary directory: {temp_dir}")

        # Initialize ChromaDB in temporary directory
        client = chromadb.PersistentClient(path=temp_dir)

        # Create or get collection
        try:
            collection = client.create_collection(
                name=args.collection_name,
                metadata={"description": "CLIP embeddings from LAION dataset"})
            logger.info(f"Created new collection: {args.collection_name}")
        except ValueError:
            collection = client.get_collection(name=args.collection_name)
            logger.info(f"Using existing collection: {args.collection_name}")

        # List parquet files from mounted directory
        parquet_files = list_local_parquet_files(args.embeddings_dir,
                                                 args.prefix)
        logger.info(f"Found {len(parquet_files)} parquet files")

        # Process each parquet file
        for parquet_file in tqdm(parquet_files, desc="Processing files"):
            logger.info(f"Processing {parquet_file}")
            try:
                df = read_parquet_file(parquet_file)

                # Process in batches
                for i in tqdm(range(0, len(df), args.batch_size),
                              desc="Processing batches"):
                    batch_df = df.iloc[i:i + args.batch_size]
                    process_batch(collection, batch_df)

            except Exception as e:
                logger.error(f"Error processing file {parquet_file}: {str(e)}")
                continue

        logger.info("Vector database build complete!")
        logger.info(f"Total documents in collection: {collection.count()}")

        # Copy the completed database to the final location
        logger.info(f"Copying database to final location: {args.persist_dir}")
        if os.path.exists(args.persist_dir):
            logger.info("Removing existing database directory")
            shutil.rmtree(args.persist_dir)
        shutil.copytree(temp_dir, args.persist_dir)
        logger.info("Database copy complete!")


if __name__ == "__main__":
    main()
