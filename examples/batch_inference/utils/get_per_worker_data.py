import argparse
import os
import math
from typing import List

def get_per_worker_chunk_paths(chunk_paths: List[str], num_workers: int) -> List[List[str]]:
    # Spread data paths among workers
    per_worker_chunk_paths = []
    per_worker_num_chunks = math.ceil(len(chunk_paths) / num_workers)
    for i in range(num_workers):
        per_worker_chunk_paths.append(chunk_paths[i * per_worker_num_chunks:(i + 1) * per_worker_num_chunks])
    return per_worker_chunk_paths

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-metadata', type=str, required=True, help='The file that contains all paths to data chunks.')
    parser.add_argument('--num-workers', type=int, required=True, help='The number of workers to be created.')
    args = parser.parse_args()

    with open(args.data_metadata, 'r') as f:
        data_paths = f.readlines()
        data_paths = [path.strip() for path in data_paths]

    per_worker_chunk_paths = get_per_worker_chunk_paths(data_paths, args.num_workers)

    # Save data groups to different files
    os.makedirs('./workers', exist_ok=True)
    for i, worker_chunk_paths in enumerate(per_worker_chunk_paths):
        with open(f'./workers/{i}.txt', 'w') as f:
            f.write('\n'.join(worker_chunk_paths))
