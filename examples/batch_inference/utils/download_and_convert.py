from datasets import load_dataset

# Login using e.g. `huggingface-cli login` to access this dataset
ds = load_dataset('lmsys/lmsys-chat-1m')


NUM_FILES = 200
dataset_len = len(ds['train'])
print(f'Dataset length: {dataset_len}')

for i in range(NUM_FILES):
    start_idx = i * dataset_len // NUM_FILES
    end_idx = (i + 1) * dataset_len // NUM_FILES
    subset = ds['train'].select(range(start_idx, end_idx))
    print(f'Processing {start_idx} to {end_idx}')
    subset.to_json(f'data/part_{i}.jsonl', orient='records', lines=True)
    with open(f'data/metadata.txt', 'a') as f:
        f.write(f'part_{i}.jsonl\n')


