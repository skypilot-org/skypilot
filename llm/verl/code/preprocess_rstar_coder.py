# Copyright 2025 MIT
"""
Preprocess rStar-Coder dataset.

"""

import argparse
import datasets
import os

from verl.utils.hdfs_io import copy
from verl.utils.hdfs_io import makedirs

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--local_dir", default=None)
    parser.add_argument("--hdfs_dir", default=None)
    parser.add_argument("--local_save_dir", default="~/data/rstar_coder")

    args = parser.parse_args()

    data_source = "microsoft/rStar-Coder"

    dataset = datasets.load_dataset(
        data_source,
        data_files="synthetic_sft/data-00000-of-00015.parquet",
        split="train",
        trust_remote_code=True)

    data_source = 'openai/gsm8k'

    # Split into train/test (90/10)
    split_dataset = dataset.train_test_split(test_size=0.1, seed=42)
    train_dataset = split_dataset["train"]
    test_dataset = split_dataset["test"]

    instruction_following = 'Let\'s think step by step and output the final answer after "####".'

    def make_map_fn(split):

        def process_fn(doc, idx):
            question_raw = doc.get("question", "")
            question = question_raw + " " + instruction_following
            answer = doc.get("response") or doc.get("code", "")

            data = {
                "data_source": data_source,
                "prompt": [{
                    "role": "user",
                    "content": question
                }],
                "ability": "code",
                "reward_model": {
                    "style": "rule",
                    "ground_truth": answer
                },
                "extra_info": {
                    "split": split,
                    "index": idx,
                }
            }
            return data

        return process_fn

    train_dataset = train_dataset.map(function=make_map_fn("train"),
                                      with_indices=True)
    test_dataset = test_dataset.map(function=make_map_fn("test"),
                                    with_indices=True)

    hdfs_dir = args.hdfs_dir
    local_save_dir = args.local_dir
    if local_save_dir is not None:
        print(
            "Warning: Argument 'local_dir' is deprecated. Please use 'local_save_dir' instead."
        )
    else:
        local_save_dir = args.local_save_dir

    train_dataset.to_parquet(os.path.join(local_save_dir, "train.parquet"))
    test_dataset.to_parquet(os.path.join(local_save_dir, "test.parquet"))

    if hdfs_dir is not None:
        makedirs(hdfs_dir)
        copy(src=local_save_dir, dst=hdfs_dir)
