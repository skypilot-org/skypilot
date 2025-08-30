import argparse
import logging
from queue import Queue
from threading import Thread

import numpy as np
import pandas as pd
import redis
from redisvl.index import SearchIndex
from sentence_transformers import SentenceTransformer
from tqdm import tqdm

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input-file', required=True)
    parser.add_argument('--schema-file', required=True)
    parser.add_argument('--start-idx', type=int, required=True)
    parser.add_argument('--end-idx', type=int, required=True)
    parser.add_argument('--batch-size', type=int, default=256)
    parser.add_argument('--model-name',
                        default='sentence-transformers/all-MiniLM-L6-v2')
    parser.add_argument('--redis-host', required=True)
    parser.add_argument('--redis-port', type=int, required=True)
    parser.add_argument('--redis-user', required=True)
    parser.add_argument('--redis-password', required=True)
    return parser.parse_args()


def create_redis_client(host, port, username, password):
    client = redis.Redis(host=host,
                         port=port,
                         username=username,
                         password=password,
                         decode_responses=True)
    client.ping()
    return client


def init_index(schema_file, redis_client):
    index = SearchIndex.from_yaml(schema_file, redis_client=redis_client)
    try:
        index.create(overwrite=False)
        logger.info("Created new Redis index")
    except Exception as e:
        if "already exists" not in str(e).lower():
            raise
        logger.info("Using existing Redis index")
    return index


def process_paper(row, embedding):
    return {
        'id': f"paper:{row['id']}",
        'title': row['title'],
        'abstract': row['abstract'],
        'authors': row['authors'],
        'venue': row['venue'],
        'year': safe_int(row['year'], 2000),
        'n_citation': safe_int(row['n_citation'], 0),
        'paper_embedding': np.array(embedding, dtype=np.float32).tobytes()
    }


def safe_int(value, default):
    if pd.notna(value) and str(value).isdigit():
        return int(value)
    return default


def redis_writer(queue, index):
    while True:
        batch = queue.get()
        if batch is None:
            break
        try:
            index.load(batch, id_field='id')
            logger.info(f"Streamed {len(batch)} papers to Redis")
        except Exception as e:
            logger.error(f"Failed to load batch: {e}")
        queue.task_done()


def main():
    args = parse_args()

    df = pd.read_csv(args.input_file,
                     encoding='utf-8',
                     encoding_errors='replace')
    total_records = len(df)

    if args.start_idx >= total_records:
        logger.warning(
            f"Start index {args.start_idx} >= total records {total_records}")
        return

    end_idx = min(args.end_idx, total_records)
    logger.info(
        f"Processing records {args.start_idx}-{end_idx} of {total_records}")

    redis_client = create_redis_client(args.redis_host, args.redis_port,
                                       args.redis_user, args.redis_password)
    index = init_index(args.schema_file, redis_client)

    redis_queue = Queue(maxsize=10)
    writer_thread = Thread(target=redis_writer, args=(redis_queue, index))
    writer_thread.start()

    model = SentenceTransformer(args.model_name)
    df_partition = df.iloc[args.start_idx:end_idx]

    batch_docs = []
    texts_batch = []
    rows_batch = []

    with tqdm(total=len(df_partition),
              desc=f"Records {args.start_idx}-{end_idx}") as pbar:
        for _, row in df_partition.iterrows():
            texts_batch.append(f"{row['title']} {row['abstract']}")
            rows_batch.append(row)

            if len(texts_batch) >= args.batch_size:
                embeddings = model.encode(texts_batch,
                                          normalize_embeddings=True)

                for row, embedding in zip(rows_batch, embeddings):
                    batch_docs.append(process_paper(row, embedding))

                texts_batch = []
                rows_batch = []
                pbar.update(args.batch_size)

                if len(batch_docs) >= args.batch_size:
                    redis_queue.put(batch_docs.copy())
                    batch_docs = []

        if texts_batch:
            embeddings = model.encode(texts_batch, normalize_embeddings=True)
            for row, embedding in zip(rows_batch, embeddings):
                batch_docs.append(process_paper(row, embedding))
            pbar.update(len(texts_batch))

        if batch_docs:
            redis_queue.put(batch_docs.copy())

    redis_queue.join()
    redis_queue.put(None)
    writer_thread.join()

    logger.info(f"Completed records {args.start_idx}-{end_idx}")


if __name__ == "__main__":
    main()
