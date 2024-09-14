import argparse
import os
import math

def chunk_data(data_paths: str, num_chunks: int):
    # Chunk data paths in to multiple chunks
    data_chunks = []
    chunk_size = math.ceil(len(data_paths) / num_chunks)
    for i in range(num_chunks):
        data_chunks.append(data_paths[i * chunk_size:(i + 1) * chunk_size])
    return data_chunks

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-paths-file', type=str, required=True, help='The file that contains all paths to data to be chunked.')
    parser.add_argument('--num-chunks', type=int, required=True, help='The number of chunks to be created.')
    args = parser.parse_args()

    with open(args.data_paths_file, 'r') as f:
        data_paths = f.readlines()
        data_paths = [path.strip() for path in data_paths]

    data_chunks = chunk_data(data_paths, args.num_chunks)

    # Save data chunks to different files
    os.makedirs('./chunks', exist_ok=True)
    for i, data_chunk in enumerate(data_chunks):
        with open(f'./chunks/{i}.txt', 'w') as f:
            f.write('\n'.join(data_chunk))
