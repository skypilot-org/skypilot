import argparse
import json
import os
import pathlib
from typing import List

from vllm import LLM, SamplingParams

DATA_PATH = '/data'
OUTPUT_PATH = '/output'

SAMPLING_PARAMS = SamplingParams(temperature=0.2,
                                 max_tokens=100,
                                 min_p=0.15,
                                 top_p=0.85)
BATCH_TOKEN_SIZE = 100000

def continue_batch_inference(llm: LLM, data_paths: List[str]):
    # Automatically skip processed data, resume the rest.
    for data_path in data_paths:
        data_name = data_path.split('/')[-1]
        succeed_indicator = os.path.join(OUTPUT_PATH, data_name + '.succeed')
        if os.path.exists(succeed_indicator):
            print(f'Skipping {data_path} because it has been processed.')
            continue

        batch_inference(llm, data_path)
        mark_as_done(succeed_indicator)


def batch_inference(llm: LLM, data_path: str):
    print(f'Processing {data_path}...')
    data_name = data_path.split('/')[-1]

    # Read data (jsonl), each line is a json object
    with open(data_path, 'r') as f:
        prompts = f.readlines()
        prompts = [json.loads(prompt.strip()) for prompt in prompts]

    # Run inference
    batch_token_size = 0
    batch_prompts = []
    predictions = []
    for i, prompt in enumerate(prompts):
        batch_token_size += len(prompt)
        if batch_token_size > BATCH_TOKEN_SIZE:
            predictions.extend(llm.generate(batch_prompts, SAMPLING_PARAMS))
            batch_prompts = []
            batch_token_size = 0

        batch_prompts.append(prompt)

    # Save predictions
    with open(os.path.join(OUTPUT_PATH, data_name), 'w') as f:
        for prediction in predictions:
            f.write(json.dumps(prediction) + '\n')


def mark_as_done(succeed_indicator: str):
    pathlib.Path(succeed_indicator).touch()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--model-name', type=str, required=True, help='The name of the model to be used for inference.')
    parser.add_argument('--num-gpus', type=int, required=True, help='The number of GPUs to be used for inference.')
    parser.add_argument('--data-chunk-file', type=str, required=True, help='The file path that contains the paths to data chunks to be processed.')
    args = parser.parse_args()

    # Load model
    llm = LLM(args.model_name, tensor_parallel_size=args.num_gpus)

    # Read data paths
    with open(args.data_chunk_file, 'r') as f:
        data_paths = f.readlines()
        data_paths = [f'{DATA_PATH}/{data_path.strip()}' for data_path in data_paths]

    continue_batch_inference(llm, data_paths)

