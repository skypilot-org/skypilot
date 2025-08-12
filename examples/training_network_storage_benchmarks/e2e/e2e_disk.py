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
from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime
import functools
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import time
from typing import Optional

from accelerate import Accelerator
from accelerate import ProfileKwargs
from datasets import load_dataset
from peft import get_peft_model
from peft import LoraConfig
from transformers import AutoModel
from transformers import AutoModelForCausalLM
from transformers import AutoModelForImageTextToText
from transformers import AutoTokenizer
from transformers import Mxfp4Config
from trl import SFTConfig
from trl import SFTTrainer


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
    def __init__(self, *args, checkpoint_dirs=None, accelerator_profiler=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.checkpoint_save_times = []
        self.total_checkpoint_save_time = 0.0
        # Add batch sampling timing tracking
        self.batch_sample_times = []
        self.total_batch_sample_time = 0.0
        # Add training step timing tracking
        self.training_step_times = []
        self.total_training_step_time = 0.0
        self.accelerator_profiler = accelerator_profiler

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

    def get_batch_samples(self, *args, **kwargs):
        # Time the batch sampling operation
        batch_timer = TimerContext()
        batch_timer.__enter__()
        result = super().get_batch_samples(*args, **kwargs)
        batch_timer.__exit__(None, None, None)
        
        # Store the timing information
        sample_time = batch_timer.get_elapsed_time()
        self.batch_sample_times.append(sample_time)
        self.total_batch_sample_time += sample_time
        # print(f"Batch sample time: {sample_time:.2f}s (Total: {self.total_batch_sample_time:.2f}s)")
        
        return result

    # Override internal checkpoint saving
    def _save_checkpoint(self, model, trial):
        # Time the checkpoint saving operation
        checkpoint_timer = TimerContext('Save checkpoint')
        with checkpoint_timer:
            # Save to the primary output directory first
            super()._save_checkpoint(model, trial)
        
        # Store the timing information
        save_time = checkpoint_timer.get_elapsed_time()
        self.checkpoint_save_times.append(save_time)
        self.total_checkpoint_save_time += save_time
        print(f"Checkpoint save time: {save_time:.2f}s (Total: {self.total_checkpoint_save_time:.2f}s)")
    
    def get_checkpoint_stats(self):
        """Get checkpoint save statistics."""
        if not self.checkpoint_save_times:
            return {
                'num_checkpoints': 0,
                'total_save_time': 0.0,
                'average_save_time': 0.0,
                'min_save_time': 0.0,
                'max_save_time': 0.0,
                'individual_save_times': []
            }
        
        return {
            'num_checkpoints': len(self.checkpoint_save_times),
            'total_save_time': self.total_checkpoint_save_time,
            'average_save_time': sum(self.checkpoint_save_times) / len(self.checkpoint_save_times),
            'min_save_time': min(self.checkpoint_save_times),
            'max_save_time': max(self.checkpoint_save_times),
            'individual_save_times': self.checkpoint_save_times.copy()
        }
    
    def get_batch_sample_stats(self):
        """Get batch sample statistics."""
        if not self.batch_sample_times:
            return {
                'num_batch_samples': 0,
                'total_sample_time': 0.0,
                'average_sample_time': 0.0,
                'min_sample_time': 0.0,
                'max_sample_time': 0.0,
                'individual_sample_times': []
            }
        
        return {
            'num_batch_samples': len(self.batch_sample_times),
            'total_sample_time': self.total_batch_sample_time,
            'average_sample_time': sum(self.batch_sample_times) / len(self.batch_sample_times),
            'min_sample_time': min(self.batch_sample_times),
            'max_sample_time': max(self.batch_sample_times),
            'individual_sample_times': self.batch_sample_times.copy()
        }
    
    def get_training_step_stats(self):
        """Get training step statistics."""
        if not self.training_step_times:
            return {
                'num_training_steps': 0,
                'total_step_time': 0.0,
                'average_step_time': 0.0,
                'min_step_time': 0.0,
                'max_step_time': 0.0,
                'individual_step_times': []
            }
        
        return {
            'num_training_steps': len(self.training_step_times),
            'total_step_time': self.total_training_step_time,
            'average_step_time': sum(self.training_step_times) / len(self.training_step_times),
            'min_step_time': min(self.training_step_times),
            'max_step_time': max(self.training_step_times),
            'individual_step_times': self.training_step_times.copy()
        }

@dataclass
class RunInfo:
    run_id: int
    timestamp: str
    dataset_name: str
    checkpoint_dir: Optional[str]
    model_id: str
    training_status: str
    output_dir: str
    dataset_load_time: float = 0.0
    model_load_time: float = 0.0
    training_time: float = 0.0
    total_time: float = 0.0
    error: Optional[str] = None
    # Checkpoint save timing statistics
    num_checkpoints_saved: int = 0
    total_checkpoint_save_time: float = 0.0
    average_checkpoint_save_time: float = 0.0
    min_checkpoint_save_time: float = 0.0
    max_checkpoint_save_time: float = 0.0
    individual_checkpoint_save_times: list = None
    # Batch sampling timing statistics
    num_batch_samples: int = 0
    total_batch_sample_time: float = 0.0
    average_batch_sample_time: float = 0.0
    min_batch_sample_time: float = 0.0
    max_batch_sample_time: float = 0.0
    individual_batch_sample_times: list = None
    # Training step timing statistics
    num_training_steps: int = 0
    total_training_step_time: float = 0.0
    average_training_step_time: float = 0.0
    min_training_step_time: float = 0.0
    max_training_step_time: float = 0.0
    individual_training_step_times: list = None

def calculate_timing_summary(all_runs):
    """Calculate summary statistics for all timing data across runs."""
    if not all_runs:
        return {
            "summary": "No runs completed",
            "dataset_loading": {},
            "model_loading": {},
            "checkpoint_saving": {},
            "batch_sampling": {},
            "training_steps": {},
            "training": {},
            "total": {}
        }
    
    # Extract timing data
    dataset_times = [run.dataset_load_time for run in all_runs if run.dataset_load_time > 0]
    model_times = [run.model_load_time for run in all_runs if run.model_load_time > 0]
    training_times = [run.training_time for run in all_runs if run.training_time > 0]
    total_times = [run.total_time for run in all_runs if run.total_time > 0]
    
    # Checkpoint save times - aggregate from all runs
    all_checkpoint_save_times = []
    total_checkpoints_saved = 0
    total_checkpoint_save_time = 0.0
    
    for run in all_runs:
        if run.individual_checkpoint_save_times:
            all_checkpoint_save_times.extend(run.individual_checkpoint_save_times)
        total_checkpoints_saved += run.num_checkpoints_saved
        total_checkpoint_save_time += run.total_checkpoint_save_time
    
    # Batch sampling times - aggregate from all runs
    all_batch_sample_times = []
    total_batch_samples = 0
    total_batch_sample_time = 0.0
    
    for run in all_runs:
        if run.individual_batch_sample_times:
            all_batch_sample_times.extend(run.individual_batch_sample_times)
        total_batch_samples += run.num_batch_samples
        total_batch_sample_time += run.total_batch_sample_time
    
    # Training step times - aggregate from all runs
    all_training_step_times = []
    total_training_steps = 0
    total_training_step_time = 0.0
    
    for run in all_runs:
        if run.individual_training_step_times:
            all_training_step_times.extend(run.individual_training_step_times)
        total_training_steps += run.num_training_steps
        total_training_step_time += run.total_training_step_time
    
    def calc_stats(times, name):
        if not times:
            return {f"{name}_count": 0}
        return {
            f"{name}_count": len(times),
            f"{name}_average": sum(times) / len(times),
            f"{name}_min": min(times),
            f"{name}_max": max(times),
            f"{name}_total": sum(times)
        }
    
    summary = {
        "total_runs": len(all_runs),
        "dataset_loading": calc_stats(dataset_times, "dataset_load"),
        "model_loading": calc_stats(model_times, "model_load"),
        "training": calc_stats(training_times, "training"),
        "total_run_time": calc_stats(total_times, "total"),
        "checkpoint_saving": {
            "total_checkpoints_saved": total_checkpoints_saved,
            "total_checkpoint_save_time": total_checkpoint_save_time,
            "average_save_time_per_checkpoint": total_checkpoint_save_time / total_checkpoints_saved if total_checkpoints_saved > 0 else 0.0,
            "checkpoint_save_times_count": len(all_checkpoint_save_times),
            "checkpoint_save_min": min(all_checkpoint_save_times) if all_checkpoint_save_times else 0.0,
            "checkpoint_save_max": max(all_checkpoint_save_times) if all_checkpoint_save_times else 0.0,
            "checkpoint_save_average": sum(all_checkpoint_save_times) / len(all_checkpoint_save_times) if all_checkpoint_save_times else 0.0
        },
        "batch_sampling": {
            "total_batch_samples": total_batch_samples,
            "total_batch_sample_time": total_batch_sample_time,
            "average_sample_time_per_batch": total_batch_sample_time / total_batch_samples if total_batch_samples > 0 else 0.0,
            "batch_sample_times_count": len(all_batch_sample_times),
            "batch_sample_min": min(all_batch_sample_times) if all_batch_sample_times else 0.0,
            "batch_sample_max": max(all_batch_sample_times) if all_batch_sample_times else 0.0,
            "batch_sample_average": sum(all_batch_sample_times) / len(all_batch_sample_times) if all_batch_sample_times else 0.0
        },
        "training_steps": {
            "total_training_steps": total_training_steps,
            "total_training_step_time": total_training_step_time,
            "average_step_time_per_step": total_training_step_time / total_training_steps if total_training_steps > 0 else 0.0,
            "training_step_times_count": len(all_training_step_times),
            "training_step_min": min(all_training_step_times) if all_training_step_times else 0.0,
            "training_step_max": max(all_training_step_times) if all_training_step_times else 0.0,
            "training_step_average": sum(all_training_step_times) / len(all_training_step_times) if all_training_step_times else 0.0
        }
    }
    
    return summary

def print_timing_summary(summary):
    """Print a formatted timing summary."""
    print("\n" + "="*80)
    print("TRAINING PERFORMANCE SUMMARY")
    print("="*80)
    
    print(f"Total Runs Completed: {summary['total_runs']}")
    
    if summary['total_runs'] == 0:
        print("No runs completed successfully.")
        return
    
    # Dataset Loading Summary
    if summary['dataset_loading'].get('dataset_load_count', 0) > 0:
        ds = summary['dataset_loading']
        print(f"\nDataset Loading Performance:")
        print(f"  • Average time: {ds['dataset_load_average']:.2f}s")
        print(f"  • Min time: {ds['dataset_load_min']:.2f}s")
        print(f"  • Max time: {ds['dataset_load_max']:.2f}s")
        print(f"  • Total time: {ds['dataset_load_total']:.2f}s")
    
    # Model Loading Summary
    if summary['model_loading'].get('model_load_count', 0) > 0:
        ml = summary['model_loading']
        print(f"\nModel Loading Performance:")
        print(f"  • Average time: {ml['model_load_average']:.2f}s")
        print(f"  • Min time: {ml['model_load_min']:.2f}s")
        print(f"  • Max time: {ml['model_load_max']:.2f}s")
        print(f"  • Total time: {ml['model_load_total']:.2f}s")
    
    # Training Summary
    if summary['training'].get('training_count', 0) > 0:
        tr = summary['training']
        print(f"\nTraining Performance:")
        print(f"  • Average time: {tr['training_average']:.2f}s")
        print(f"  • Min time: {tr['training_min']:.2f}s")
        print(f"  • Max time: {tr['training_max']:.2f}s")
        print(f"  • Total time: {tr['training_total']:.2f}s")
    
    # Training Step Summary
    ts = summary['training_steps']
    if ts['total_training_steps'] > 0:
        print(f"\nTraining Step Performance:")
        print(f"  • Total training steps: {ts['total_training_steps']}")
        print(f"  • Average step time per step: {ts['average_step_time_per_step']:.2f}s")
        print(f"  • Min step time: {ts['training_step_min']:.2f}s")
        print(f"  • Max step time: {ts['training_step_max']:.2f}s")
        print(f"  • Total training step time: {ts['total_training_step_time']:.2f}s")
    
    # Batch Sampling Summary
    bs = summary['batch_sampling']
    if bs['total_batch_samples'] > 0:
        print(f"\nBatch Sampling Performance:")
        print(f"  • Total batch samples: {bs['total_batch_samples']}")
        print(f"  • Average sample time per batch: {bs['average_sample_time_per_batch']:.2f}s")
        print(f"  • Min sample time: {bs['batch_sample_min']:.2f}s")
        print(f"  • Max sample time: {bs['batch_sample_max']:.2f}s")
        print(f"  • Total batch sample time: {bs['total_batch_sample_time']:.2f}s")
    
    # Checkpoint Saving Summary
    cs = summary['checkpoint_saving']
    if cs['total_checkpoints_saved'] > 0:
        print(f"\nCheckpoint Saving Performance:")
        print(f"  • Total checkpoints saved: {cs['total_checkpoints_saved']}")
        print(f"  • Average save time per checkpoint: {cs['average_save_time_per_checkpoint']:.2f}s")
        print(f"  • Min save time: {cs['checkpoint_save_min']:.2f}s")
        print(f"  • Max save time: {cs['checkpoint_save_max']:.2f}s")
        print(f"  • Total checkpoint save time: {cs['total_checkpoint_save_time']:.2f}s")
    
    # Total Run Time Summary
    if summary['total_run_time'].get('total_count', 0) > 0:
        tt = summary['total_run_time']
        print(f"\nOverall Run Performance:")
        print(f"  • Average total time per run: {tt['total_average']:.2f}s")
        print(f"  • Min total time: {tt['total_min']:.2f}s")
        print(f"  • Max total time: {tt['total_max']:.2f}s")
        print(f"  • Total time across all runs: {tt['total_total']:.2f}s")
    
    print("="*80)

def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Train a model using SFT on a specified dataset")
    parser.add_argument(
        "--model_id", 
        type=str, 
        default="google/gemma-3-12b-it",
        help="Model ID to use for training (default: google/gemma-3-12b-it)"
    )
    parser.add_argument(
        "--dataset_name", 
        type=str, 
        default="open-r1/codeforces-cots",
        help="Dataset name to use for training (default: open-r1/codeforces-cots)"
    )
    parser.add_argument(
        "--dirs",
        nargs='*',
        default=['/tmp'],
        help="Additional directories to load models, load, save checkpoints to (e.g., --dirs /tmp s3://my-bucket/checkpoints)"
    )
    parser.add_argument(
        "--num_proc", 
        type=int, 
        default=None,
        help="Processes used for data/model loading"
    )
    parser.add_argument(
        "--clear_dataset_and_checkpoint",
        action="store_true",
        default=True,
        help="Clear dataset cache, model cache, and checkpoint directories before training (default: True)"
    )
    parser.add_argument(
        "--no_clear_dataset_and_checkpoint",
        dest="clear_dataset_and_checkpoint",
        action="store_false",
        help="Don't clear dataset cache, model cache, and checkpoint directories before training"
    )
    parser.add_argument(
        "--enable_lora",
        action="store_true",
        default=False,
        help="Enable LoRA fine-tuning"
    )
    parser.add_argument(
        "--enable_profiling",
        action="store_true",
        default=False,
        help="Enable accelerate profiling with chrome trace export"
    )
    parser.add_argument(
        "--gradient_accumulation_steps",
        type=int,
        default=8,
        help="Number of gradient accumulation steps (default: 8)"
    )
    parser.add_argument(
        "--per_device_train_batch_size",
        type=int,
        default=1,
        help="Training batch size per device (default: 1)"
    )
    parser.add_argument(
        "--skip_training",
        action="store_true",
        default=False,
        help="Skip the training step and only load model/dataset"
    )
    args = parser.parse_args()

    num_proc = args.num_proc
    if num_proc == -1:
        num_proc = os.cpu_count()
    elif num_proc is None:
        num_proc = int(os.cpu_count() / 2)

    # List to track all training runs
    all_runs = []

    # Setup profiling if enabled
    accelerator_kwargs = {}
    if args.enable_profiling:
        def trace_handler(p):
            # output = p.key_averages().table(sort_by="self_cuda_time_total", row_limit=10)
            # print(output)
            trace_filename = f"/tmp/trace_{p.step_num}_{local_process_index}.json"
            p.export_chrome_trace(trace_filename)
            print(f"Chrome trace exported to: {trace_filename}")

        profile_kwargs = ProfileKwargs(
            activities=["cpu", "cuda"],
            schedule_option={"wait": 0, "warmup": 1, "active": 3, "repeat": 0, "skip_first": 6},
            on_trace_ready=trace_handler
        )
        accelerator_kwargs['kwargs_handlers'] = [profile_kwargs]

    accelerator = Accelerator(**accelerator_kwargs)
    local_process_index = accelerator.local_process_index

    # Determine model class and configuration based on model_id
    model_class = AutoModel
    quantization_config = None
    device_map_args = {}
    
    if args.model_id == 'google/gemma-3-12b-it':
        model_class = AutoModelForImageTextToText
    elif args.model_id in ['openai/gpt-oss-120b', 'openai/gpt-oss-20b']:
        model_class = AutoModelForCausalLM
        quantization_config = Mxfp4Config(dequantize=True)
        if args.enable_lora:
            device_map_args = {'device_map': 'auto'}

    # Iterate over each directory
    for i, base_path in enumerate(args.dirs):
        print(f"\n=== Starting Run {i+1}/{len(args.dirs)} ===")
        
        # Convert base_path to Path object
        base_path = Path(base_path)
        
        # Check if checkpoint exists, skip run if not
        if not base_path.exists():
            print(f"Base path {base_path} does not exist. Skipping run {i+1}")
            continue
        
        # Set up all paths at the beginning of the run
        # 1. Dataset path/name - use HuggingFaceH4/Multilingual-Thinking for all models
        dataset_name = "HuggingFaceH4/Multilingual-Thinking"
        
        # 2. Output directory path (for saving the model)
        output_dir = base_path

        # 4. Cache directories
        dataset_cache_dir = output_dir / "dataset_cache"
        model_cache_dir = output_dir / "model_cache"
        checkpoint_saving_dir = output_dir / "checkpoints" / f"{accelerator.process_index}"

        print(f"Dataset: {dataset_name}")
        print(f"Dataset cache: {dataset_cache_dir}")
        print(f"Model cache: {model_cache_dir}")
        print(f"Checkpoint saving dir: {checkpoint_saving_dir}")

        with accelerator.local_main_process_first():
            try:
                if checkpoint_saving_dir.exists():
                    shutil.rmtree(checkpoint_saving_dir)
                checkpoint_saving_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                print(f'Removing checkpoint exception {e}')

        # Create run info
        run_info = RunInfo(
            run_id=i + 1,
            timestamp=datetime.now().isoformat(),
            dataset_name=dataset_name,
            checkpoint_dir=str(base_path),
            model_id=args.model_id,
            training_status="started",
            output_dir=str(output_dir),
        )

        dataset_timer = TimerContext('Load dataset')
        with dataset_timer:
            train_dataset = load_dataset(dataset_name, split="train", cache_dir=str(dataset_cache_dir), num_proc=num_proc)
            # Remove prompt column for original codeforces dataset
            if dataset_name == "open-r1/codeforces-cots" and "prompt" in train_dataset.column_names:
                train_dataset = train_dataset.remove_columns("prompt")
        
        run_info.dataset_load_time = dataset_timer.get_elapsed_time()

        # Load model (now benchmarking actual load time)
        model_timer = TimerContext('Load model')
        with model_timer:
            print(f"Loading model from cache directory: {model_cache_dir}")
            
            # Prepare model loading arguments
            model_kwargs = {
                "cache_dir": str(model_cache_dir),
                "attn_implementation": "eager",
                "torch_dtype": "auto",
                "use_cache": False,
            }
            
            # Add quantization config for GPT-OSS models
            if quantization_config is not None:
                model_kwargs["quantization_config"] = quantization_config
            
            # Add device map for LoRA
            model_kwargs.update(device_map_args)
            
            # Add local_files_only for non-GPT-OSS models
            if args.model_id not in ['openai/gpt-oss-120b', 'openai/gpt-oss-20b']:
                model_kwargs["local_files_only"] = True
            
            model = model_class.from_pretrained(args.model_id, **model_kwargs)
        
        run_info.model_load_time = model_timer.get_elapsed_time()
        print(f'Loaded model: {args.model_id}')
        
        # Apply LoRA if enabled
        if args.enable_lora:
            lora_timer = TimerContext('Apply LoRA')
            with lora_timer:
                num_layers = 0
                target_parameters = []
                
                if args.model_id == 'openai/gpt-oss-120b':
                    num_layers = 36
                elif args.model_id == 'openai/gpt-oss-20b':
                    num_layers = 24

                for layer_idx in range(num_layers):
                    target_parameters.append(f'{layer_idx}.mlp.experts.gate_up_proj')
                    target_parameters.append(f'{layer_idx}.mlp.experts.down_proj')

                peft_config = LoraConfig(
                    r=8,
                    lora_alpha=16,
                    target_modules="all-linear",
                    target_parameters=target_parameters,
                )
                model = get_peft_model(model, peft_config)
                model.print_trainable_parameters()
        
        if args.skip_training:
            print(f"Skipping training for run {i+1} as requested.")
            print(f"Model loaded: {args.model_id}")
            print(f"Dataset loaded: {len(train_dataset)} samples")
            print(f"Dataset load time: {run_info.dataset_load_time:.2f}s")
            print(f"Model load time: {run_info.model_load_time:.2f}s")
            
            # Update run info for skipped training
            run_info.training_status = "skipped"
            run_info.training_time = 0.0
            run_info.total_time = run_info.dataset_load_time + run_info.model_load_time
            
            # Save run info to JSON file in the output directory
            json_path = output_dir / f"training_run_{local_process_index}_{i+1}_info.json"
            with open(json_path, 'w+') as f:
                json.dump(asdict(run_info), f, indent=2)
            print(f"Saved training info to: {json_path}")
            
            # Add to summary
            all_runs.append(run_info)
            
            print(f"Completed run {i+1}/{len(args.dirs)} (skipped training)")
            del model
            continue
        
        # Configure training arguments based on model type
        if args.model_id in ['openai/gpt-oss-120b', 'openai/gpt-oss-20b']:
            training_args = SFTConfig(
                output_dir=str(checkpoint_saving_dir),
                learning_rate=2e-4,
                num_train_epochs=1,
                logging_steps=1,
                per_device_train_batch_size=args.per_device_train_batch_size,
                gradient_accumulation_steps=args.gradient_accumulation_steps,
                max_length=1024,
                warmup_ratio=0.03,
                lr_scheduler_type="cosine_with_min_lr",
                lr_scheduler_kwargs={"min_lr_rate": 0.1},
                dataset_num_proc=num_proc,
                save_strategy='steps',
                save_steps=5,
                save_safetensors=False,
            )
        else:
            training_args = SFTConfig(
                output_dir=str(checkpoint_saving_dir),
                bf16=True,
                use_liger_kernel=True,
                # gradient_checkpointing=True,
                # gradient_checkpointing_kwargs={"use_reentrant": False},
                max_length=8192,
                per_device_train_batch_size=args.per_device_train_batch_size,
                gradient_accumulation_steps=args.gradient_accumulation_steps,
                dataset_num_proc=num_proc,
                num_train_epochs=5,
                max_steps=25,  # 25 steps per epoch * 4 epochs = 100 total steps
                # save_strategy='epoch',
                save_strategy='steps',
                save_steps=5,
                # save_total_limit=3,
                save_safetensors=False,
            )
        
        # Train model
        trainer_kwargs = {
            'args': training_args,
            'model': model,
            'train_dataset': train_dataset,
        }
        
        training_timer = TimerContext('Training')
        with training_timer:
            if args.enable_profiling:
                with accelerator.profile() as prof:
                    trainer_kwargs['accelerator_profiler'] = prof
                    trainer = ProfilingSFTTrainer(**trainer_kwargs)
                    trainer.train()
            else:
                trainer = ProfilingSFTTrainer(**trainer_kwargs)
                trainer.train()
        
        run_info.training_time = training_timer.get_elapsed_time()
        run_info.training_status = "completed"
        
        # Capture checkpoint save statistics
        checkpoint_stats = trainer.get_checkpoint_stats()
        run_info.num_checkpoints_saved = checkpoint_stats['num_checkpoints']
        run_info.total_checkpoint_save_time = checkpoint_stats['total_save_time']
        run_info.average_checkpoint_save_time = checkpoint_stats['average_save_time']
        run_info.min_checkpoint_save_time = checkpoint_stats['min_save_time']
        run_info.max_checkpoint_save_time = checkpoint_stats['max_save_time']
        run_info.individual_checkpoint_save_times = checkpoint_stats['individual_save_times']
        
        # Capture batch sampling statistics
        batch_sample_stats = trainer.get_batch_sample_stats()
        run_info.num_batch_samples = batch_sample_stats['num_batch_samples']
        run_info.total_batch_sample_time = batch_sample_stats['total_sample_time']
        run_info.average_batch_sample_time = batch_sample_stats['average_sample_time']
        run_info.min_batch_sample_time = batch_sample_stats['min_sample_time']
        run_info.max_batch_sample_time = batch_sample_stats['max_sample_time']
        run_info.individual_batch_sample_times = batch_sample_stats['individual_sample_times']
        
        # Capture training step statistics
        training_step_stats = trainer.get_training_step_stats()
        run_info.num_training_steps = training_step_stats['num_training_steps']
        run_info.total_training_step_time = training_step_stats['total_step_time']
        run_info.average_training_step_time = training_step_stats['average_step_time']
        run_info.min_training_step_time = training_step_stats['min_step_time']
        run_info.max_training_step_time = training_step_stats['max_step_time']
        run_info.individual_training_step_times = training_step_stats['individual_step_times']
        
        run_info.total_time = run_info.dataset_load_time + run_info.model_load_time + run_info.training_time
        
        # Print checkpoint save statistics summary
        if run_info.num_checkpoints_saved > 0:
            print(f"Checkpoint Save Statistics:")
            print(f"  - Number of checkpoints saved: {run_info.num_checkpoints_saved}")
            print(f"  - Total checkpoint save time: {run_info.total_checkpoint_save_time:.2f}s")
            print(f"  - Average save time per checkpoint: {run_info.average_checkpoint_save_time:.2f}s")
            print(f"  - Min save time: {run_info.min_checkpoint_save_time:.2f}s")
            print(f"  - Max save time: {run_info.max_checkpoint_save_time:.2f}s")
        else:
            print("No checkpoints were saved during training")
        
        # Print batch sampling statistics summary
        if run_info.num_batch_samples > 0:
            print(f"Batch Sampling Performance:")
            print(f"  - Number of batch samples: {run_info.num_batch_samples}")
            print(f"  - Total batch sample time: {run_info.total_batch_sample_time:.2f}s")
            print(f"  - Average batch sample time: {run_info.average_batch_sample_time:.2f}s")
            print(f"  - Min batch sample time: {run_info.min_batch_sample_time:.2f}s")
            print(f"  - Max batch sample time: {run_info.max_batch_sample_time:.2f}s")
        else:
            print("No batch samples were processed during training")
        
        # Print training step statistics summary
        if run_info.num_training_steps > 0:
            print(f"Training Step Performance:")
            print(f"  - Number of training steps: {run_info.num_training_steps}")
            print(f"  - Total training step time: {run_info.total_training_step_time:.2f}s")
            print(f"  - Average training step time: {run_info.average_training_step_time:.2f}s")
            print(f"  - Min training step time: {run_info.min_training_step_time:.2f}s")
            print(f"  - Max training step time: {run_info.max_training_step_time:.2f}s")
        else:
            print("No training steps were executed during training")
        
        print(f"Training completed successfully for run {i+1}")
        
        # Save run info to JSON file in the output directory
        json_path = output_dir / f"training_run_{local_process_index}_{i+1}_info.json"
        with open(json_path, 'w+') as f:
            json.dump(asdict(run_info), f, indent=2)
        print(f"Saved training info to: {json_path}")
        
        # Add to summary
        all_runs.append(run_info)
        
        print(f"Completed run {i+1}/{len(args.dirs)}")
        del model
        del trainer
    
    # Save overall summary
    summary_path = Path(f"all_training_runs_summary_{local_process_index}.json")
    with open(summary_path, 'w+') as f:
        json.dump([asdict(run) for run in all_runs], f, indent=2)
    print(f"\nSaved overall training summary to: {summary_path}")

    # Calculate and print timing summary
    timing_summary = calculate_timing_summary(all_runs)
    print_timing_summary(timing_summary)
    
    # Save timing summary to JSON file
    timing_summary_path = Path(f"timing_summary_{local_process_index}.json")
    with open(timing_summary_path, 'w+') as f:
        json.dump(timing_summary, f, indent=2)
    print(f"Saved timing summary to: {timing_summary_path}")

    # Cat the JSON files only if this is the main process
    if local_process_index == 0:
        print("\n" + "="*80)
        print("JSON OUTPUT FROM MAIN PROCESS")
        print("="*80)
        
        # Cat individual training run files
        for i, dir_path in enumerate(args.dirs):
            individual_json_path = Path(dir_path) / f"training_run_{local_process_index}_{i+1}_info.json"
            if individual_json_path.exists():
                print(f"\n--- Training Run {i+1} Info (Directory: {dir_path}) ---")
                with open(individual_json_path, 'r') as f:
                    print(f.read())
        
        # Cat overall summary file
        if summary_path.exists():
            print(f"\n--- All Training Runs Summary ---")
            with open(summary_path, 'r') as f:
                print(f.read())
        
        # Cat timing summary file
        if timing_summary_path.exists():
            print(f"\n--- Timing Summary ---")
            with open(timing_summary_path, 'r') as f:
                print(f.read())
        
        print("="*80)

if __name__ == "__main__":
    main()