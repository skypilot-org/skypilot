"""Train GPT-OSS on the HuggingFace Multilingual-Thinking dataset.

accelerate launch --config_file \
    examples/accelerate_configs/deepspeed_zero3.yaml \
    examples/scripts/sft_gpt_oss.py
"""

import argparse
import functools
import os
import time

from accelerate import Accelerator
from accelerate import ProfileKwargs
from datasets import load_dataset
from peft import get_peft_model
from peft import LoraConfig
from transformers import AutoModelForCausalLM
from transformers import Mxfp4Config
from trl import SFTConfig
from trl import SFTTrainer


class TimerContext:
    """A timer class that can be used as a context manager to measure
    execution time.
    """

    def __init__(self, name=None):
        self.name = name
        self.start_time = None
        self.end_time = None
        self.elapsed_time = None

    def __enter__(self):
        self.start_time = time.time()
        if self.name:
            print(f'Starting {self.name}...')
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.end_time = time.time()
        self.elapsed_time = self.end_time - self.start_time

        if self.name:
            print(f'Completed {self.name} in {self.elapsed_time:.2f} seconds')

        # If there was an exception, don't suppress it
        return False

    def __call__(self, func):
        """Make TimerContext work as a decorator."""

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            if self.name:
                print(f'Starting {self.name}...')

            try:
                result = func(*args, **kwargs)
                end_time = time.time()
                elapsed_time = end_time - start_time
                if self.name:
                    print(
                        f'Completed {self.name} in {elapsed_time:.2f} seconds')
                return result
            except Exception as e:
                end_time = time.time()
                elapsed_time = end_time - start_time
                if self.name:
                    print(
                        f'Failed {self.name} after {elapsed_time:.2f} seconds')
                raise e

        return wrapper

    def get_elapsed_time(self):
        """Get elapsed time in seconds."""
        return self.elapsed_time


class ProfilingSFTTrainer(SFTTrainer):
    """Custom SFTTrainer that tracks timing statistics."""

    def __init__(self, *args, accelerator_profiler=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.accelerator_profiler = accelerator_profiler
        self.training_step_times = []
        self.total_training_step_time = 0.0

    def training_step(self, *args, **kwargs):
        # Time the training step operation
        step_timer = TimerContext()
        with step_timer:
            result = super().training_step(*args, **kwargs)

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
            print('No training steps completed.')
            return

        num_steps = len(self.training_step_times)
        avg_time = self.total_training_step_time / num_steps
        min_time = min(self.training_step_times)
        max_time = max(self.training_step_times)

        print('\n' + '=' * 60)
        print('TRAINING TIME STATISTICS')
        print('=' * 60)
        print(f'Total training steps: {num_steps}')
        print(
            f'Total training time: {self.total_training_step_time:.2f} seconds')
        print(f'Average time per step: {avg_time:.3f} seconds')
        print(f'Fastest step: {min_time:.3f} seconds')
        print(f'Slowest step: {max_time:.3f} seconds')
        print(f'Time variance: {max_time - min_time:.3f} seconds')

        # Show distribution of step times
        print('\nStep time distribution:')
        for i, step_time in enumerate(self.training_step_times):
            print(f'  Step {i+1}: {step_time:.3f}s')
        print('=' * 60)


local_process_index = 0


def main():
    global local_process_index

    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description='Train a model using SFT on Multilingual-Thinking dataset')
    parser.add_argument(
        '--model_id',
        type=str,
        default='openai/gpt-oss-120b',
        help='The model ID to use for training (default: openai/gpt-oss-120b)')
    parser.add_argument('--enable_lora',
                        action='store_true',
                        default=False,
                        help='Enable LoRA')
    parser.add_argument(
        '--enable_profiling',
        action='store_true',
        default=False,
        help='Enable accelerate profiling with chrome trace export')
    parser.add_argument(
        '--gradient_accumulation_steps',
        type=int,
        default=1,
        help='Number of gradient accumulation steps (default: 1)')
    parser.add_argument('--per_device_train_batch_size',
                        type=int,
                        default=1,
                        help='Training batch size per device (default: 1)')
    args = parser.parse_args()

    # Setup profiling if enabled
    accelerator_kwargs = {}
    if args.enable_profiling:

        def trace_handler(p):
            output = p.key_averages().table(sort_by='self_cuda_time_total',
                                            row_limit=10)
            print(output)
            p.export_chrome_trace(
                f'/tmp/trace_{p.step_num}_{local_process_index}.json')

        profile_kwargs = ProfileKwargs(activities=['cpu', 'cuda'],
                                       schedule_option={
                                           'wait': 1,
                                           'warmup': 1,
                                           'active': 1,
                                           'repeat': 1,
                                           'skip_first': 1
                                       },
                                       on_trace_ready=trace_handler)
        accelerator_kwargs['kwargs_handlers'] = [profile_kwargs]

    accelerator = Accelerator(**accelerator_kwargs)
    local_process_index = accelerator.local_process_index
    model_id = args.model_id

    # Load dataset
    num_proc = int(os.cpu_count() / 2)
    train_dataset = load_dataset('HuggingFaceH4/Multilingual-Thinking',
                                 split='train',
                                 num_proc=num_proc)

    quantization_config = Mxfp4Config(dequantize=True)

    device_map_args = {}
    if args.enable_lora:
        device_map_args = {'device_map': 'auto'}

    # Load model
    model = AutoModelForCausalLM.from_pretrained(
        model_id,
        attn_implementation='eager',
        torch_dtype='auto',
        use_cache=False,
        quantization_config=quantization_config,
        **device_map_args,
    )

    print(f'Loaded model: {args.model_id}')

    if args.enable_lora:
        num_layers = 0
        target_parameters = []
        if args.model_id == 'openai/gpt-oss-120b':
            num_layers = 36
        elif args.model_id == 'openai/gpt-oss-20b':
            num_layers = 24

        for i in range(num_layers):
            target_parameters.append(f'{i}.mlp.experts.gate_up_proj')
            target_parameters.append(f'{i}.mlp.experts.down_proj')

        peft_config = LoraConfig(
            r=8,
            lora_alpha=16,
            target_modules='all-linear',
            target_parameters=target_parameters,
        )
        model = get_peft_model(model, peft_config)
        model.print_trainable_parameters()

    # Train model
    training_args = SFTConfig(
        output_dir=f'{model_id}-checkpoint',
        learning_rate=2e-4,
        num_train_epochs=1,
        logging_steps=1,
        per_device_train_batch_size=args.per_device_train_batch_size,
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        max_length=1024,
        warmup_ratio=0.03,
        lr_scheduler_type='cosine_with_min_lr',
        lr_scheduler_kwargs={'min_lr_rate': 0.1},
        dataset_num_proc=num_proc,
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


if __name__ == '__main__':
    main()
