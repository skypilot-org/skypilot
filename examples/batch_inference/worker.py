import argparse
import json
import os
import pathlib
from typing import List

from .inference import DATA_PATH, OUTPUT_PATH, batch_inference
from vllm import LLM


def continue_batch_inference(llm: LLM, data_paths: List[str]):
    """Continue batch inference on a list of data chunks."""

    # Automatically skip processed data, resume the rest.
    for data_path in data_paths:
        data_name = data_path.split('/')[-1]
        succeed_indicator = os.path.join(OUTPUT_PATH, data_name + '.succeed')
        if os.path.exists(succeed_indicator):
            print(f'Skipping {data_path} because it has been processed.')
            continue

        batch_inference(llm, data_path)
        mark_as_done(succeed_indicator)


def mark_as_done(succeed_indicator: str):
    """Mark data processing as done with a indicator file."""
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

