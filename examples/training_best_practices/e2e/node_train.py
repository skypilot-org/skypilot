# Copyright 2020-2025 The HuggingFace Team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# /// script
# dependencies = [
#     "trl @ git+https://github.com/huggingface/trl.git",
# ]
# ///

"""
Train Gemma-3 on the Codeforces COTS dataset.

accelerate launch --config_file examples/accelerate_configs/deepspeed_zero3.yaml examples/scripts/sft_gemma3.py
"""

import argparse
from accelerate import Accelerator, ProfileKwargs
from datasets import load_dataset
from transformers import AutoModelForImageTextToText
from trl import SFTConfig
from trl import SFTTrainer
import time
import functools

class TimerContext:
    """A timer class that can be used as a context manager to measure execution time."""
    
    def __init__(self, name=None):
        self.name = name
        self.start_time = None
        self.end_time = None
        self.elapsed_time = None
    
    def __enter__(self):
        self.start_time = time.time()
        if self.name:
            print(f"Starting {self.name}...")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.end_time = time.time()
        self.elapsed_time = self.end_time - self.start_time
        
        if self.name:
            print(f"Completed {self.name} in {self.elapsed_time:.2f} seconds")
        
        # If there was an exception, don't suppress it
        return False
    
    def __call__(self, func):
        """Make TimerContext work as a decorator."""
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            if self.name:
                print(f"Starting {self.name}...")
            
            try:
                result = func(*args, **kwargs)
                end_time = time.time()
                elapsed_time = end_time - start_time
                if self.name:
                    print(f"Completed {self.name} in {elapsed_time:.2f} seconds")
                return result
            except Exception as e:
                end_time = time.time()
                elapsed_time = end_time - start_time
                if self.name:
                    print(f"Failed {self.name} after {elapsed_time:.2f} seconds")
                raise e
                
        return wrapper
    
    def get_elapsed_time(self):
        """Get elapsed time in seconds."""
        return self.elapsed_time

class ProfilingSFTTrainer(SFTTrainer):
    def __init__(self, *args, accelerator_profiler=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.accelerator_profiler = accelerator_profiler
        self.training_step_times = []
        self.total_training_step_time = 0.0

    def training_step(self, *args, **kwargs):
        # Time the training step operation
        step_timer = TimerContext()
        step_timer.__enter__()
        result = super().training_step(*args, **kwargs)
        step_timer.__exit__(None, None, None)
        
        # Store the timing information
        step_time = step_timer.get_elapsed_time()
        self.training_step_times.append(step_time)
        self.total_training_step_time += step_time
        
        if self.accelerator_profiler is not None:
            self.accelerator_profiler.step()
        return result

    def log_training_time_stats(self):
        """Log comprehensive training time statistics."""
        if not self.training_step_times:
            print("No training steps completed.")
            return
        
        num_steps = len(self.training_step_times)
        avg_time = self.total_training_step_time / num_steps
        min_time = min(self.training_step_times)
        max_time = max(self.training_step_times)
        
        print("\n" + "="*60)
        print("TRAINING TIME STATISTICS")
        print("="*60)
        print(f"Total training steps: {num_steps}")
        print(f"Total training time: {self.total_training_step_time:.2f} seconds")
        print(f"Average time per step: {avg_time:.3f} seconds")
        print(f"Fastest step: {min_time:.3f} seconds")
        print(f"Slowest step: {max_time:.3f} seconds")
        print(f"Time variance: {max_time - min_time:.3f} seconds")
        
        # Show distribution of step times
        print("\nStep time distribution:")
        for i, step_time in enumerate(self.training_step_times):
            print(f"  Step {i+1}: {step_time:.3f}s")
        print("="*60)

local_process_index = 0

def main():
    global local_process_index
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Train a model using SFT on Codeforces dataset")
    parser.add_argument(
        "--model_id",
        type=str,
        default="google/gemma-3-12b-it",
        help="The model ID to use for training (default: google/gemma-3-12b-it)"
    )
    parser.add_argument(
        "--enable_profiling",
        action="store_true",
        default=False,
        help="Enable accelerate profiling with chrome trace export"
    )
    args = parser.parse_args()
    
    # Setup profiling if enabled
    accelerator_kwargs = {}
    if args.enable_profiling:
        def trace_handler(p):
            global local_process_index
            output = p.key_averages().table(sort_by="self_cuda_time_total", row_limit=10)
            print(output)
            p.export_chrome_trace(f"/tmp/trace_{p.step_num}_{local_process_index}.json")

        profile_kwargs = ProfileKwargs(
            activities=["cpu", "cuda"],
            schedule_option={"wait": 0, "warmup": 1, "active": 3, "repeat": 0, "skip_first": 6},
            on_trace_ready=trace_handler
        )
        accelerator_kwargs['kwargs_handlers'] = [profile_kwargs]

    accelerator = Accelerator(**accelerator_kwargs)
    local_process_index = accelerator.local_process_index

    # Load dataset
    train_dataset = load_dataset("open-r1/codeforces-cots", split="train", num_proc=32)
    train_dataset = train_dataset.remove_columns("prompt")

    # Load model
    model_id = args.model_id
    model = AutoModelForImageTextToText.from_pretrained(model_id, attn_implementation="eager")

    # Train model
    training_args = SFTConfig(
        output_dir=f"{model_id}-codeforces-SFT",
        bf16=True,
        use_liger_kernel=True,
        # gradient_checkpointing=True,
        # gradient_checkpointing_kwargs={"use_reentrant": False},
        max_length=8192,
        per_device_train_batch_size=1,
        gradient_accumulation_steps=8,
        dataset_num_proc=32,
        num_train_epochs=5,
        max_steps=10,
        # save_strategy='steps',
        # save_steps=2,
    )
    
    # Train model with optional profiling
    trainer_kwargs = {
        'args': training_args,
        'model': model,
        'train_dataset': train_dataset,
    }
    
    if args.enable_profiling:
        with accelerator.profile() as prof:
            trainer_kwargs['accelerator_profiler'] = prof
            trainer = ProfilingSFTTrainer(**trainer_kwargs)
            trainer.train()
            trainer.log_training_time_stats()
    else:
        trainer = ProfilingSFTTrainer(**trainer_kwargs)
        trainer.train()
        trainer.log_training_time_stats()


if __name__ == "__main__":
    main()
