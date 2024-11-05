import argparse

from datasets import load_dataset

def main(num_chunks: int):
    # Login using e.g. `huggingface-cli login` to access this dataset
    ds = load_dataset('lmsys/lmsys-chat-1m')

    dataset_len = len(ds['train'])
    print(f'Dataset length: {dataset_len}')

    for i in range(num_chunks):
        start_idx = i * dataset_len // num_chunks
        end_idx = (i + 1) * dataset_len // num_chunks
        subset = ds['train'].select(range(start_idx, end_idx))
        print(f'Processing {start_idx} to {end_idx}')
        subset.to_json(f'/data/part_{i}.jsonl', orient='records', lines=True)
        with open(f'/data/metadata.txt', 'a') as f:
            f.write(f'/data/part_{i}.jsonl\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--num-chunks', type=int, required=True, help='The number of chunks to be created.')
    args = parser.parse_args()
    main(args.num_chunks)
