import argparse
import os
import pathlib
import typing
from typing import List

from inference import DATA_PATH, OUTPUT_PATH, batch_inference, create_model

if typing.TYPE_CHECKING:
    from vllm import LLM

def continue_batch_inference(llm: 'LLM', data_chunks: List[str]):
    """Continue batch inference on a list of data chunks."""

    # Automatically skip processed data, resume the rest.
    for data_chunk in data_chunks:
        data_name = data_chunk.split('/')[-1]
        succeed_indicator = os.path.join(OUTPUT_PATH, data_name + '.succeed')
        if os.path.exists(succeed_indicator):
            print(f'Skipping {data_chunk} because it has been processed.')
            continue

        batch_inference(llm, data_chunk)
        mark_as_done(succeed_indicator)


def mark_as_done(succeed_indicator: str):
    """Mark data processing as done with a indicator file."""
    pathlib.Path(succeed_indicator).touch()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--model-name', type=str, required=True, help='The name of the model to be used for inference.')
    parser.add_argument('--num-gpus', type=int, required=True, help='The number of GPUs to be used for inference.')
    parser.add_argument('--data-group-metadata', type=str, required=True, help='The file path that contains the metadata of data groups to be processed.')
    args = parser.parse_args()

    # Load model
    llm = create_model(args.model_name, args.num_gpus)

    # Read data paths
    with open(args.data_group_metadata, 'r') as f:
        data_chunks = f.readlines()
        data_chunks = [f'{DATA_PATH}/{data_chunk.strip()}' for data_chunk in data_chunks]

    continue_batch_inference(llm, data_chunks)

