from dataclasses import dataclass
from dataclasses import field
import os
import pathlib
import shutil
import subprocess
from typing import Optional

from datasets import load_dataset
from peft import LoraConfig
import torch
import transformers
from transformers import AutoModelForCausalLM
from transformers import AutoTokenizer
from transformers import BitsAndBytesConfig
from transformers import GemmaTokenizer
from trl import SFTTrainer


@dataclass
class ModelArguments:
    model_name_or_path: Optional[str] = field(default="google/gemma-7b")


@dataclass
class TrainingArguments(transformers.TrainingArguments):
    per_device_train_batch_size: int = field(default=1)
    gradient_accumulation_steps: int = field(default=4)
    warmup_steps: int = field(default=2)
    max_steps: int = field(default=10)
    learning_rate: float = field(default=2e-4)
    fp16: bool = field(default=True)
    logging_steps: int = field(default=1)

    output_dir: str = field(default="outputs")
    optim: str = field(default="paged_adamw_8bit")
    save_steps: int = field(default=1)


class CheckpointCallback(transformers.TrainerCallback):

    def on_save(self, args, state, control, **kwargs):
        """Add complete indicator to avoid incomplete checkpoints."""
        if state.is_world_process_zero:
            ckpt_path = os.path.join(args.output_dir,
                                     f'checkpoint-{state.global_step}')
            with open(os.path.join(ckpt_path, 'complete'), 'w') as f:
                f.write('')
            print(f'Checkpoint {state.global_step} saved.')
        torch.distributed.barrier()


def safe_save_model_for_hf_trainer(trainer: transformers.Trainer,
                                   output_dir: str):
    """Collects the state dict and dump to disk."""
    state_dict = trainer.model.state_dict()
    if trainer.args.should_save:
        cpu_state_dict = {key: value.cpu() for key, value in state_dict.items()}
        del state_dict
        trainer._save(output_dir, state_dict=cpu_state_dict)  # noqa


def cleanup_incomplete_checkpoints(output_dir):
    """Remove incomplete checkpoints."""
    checkpoints = list(pathlib.Path(output_dir).glob('checkpoint-*'))
    checkpoints = [c for c in checkpoints if c.name.split('-')[-1].isdigit()]
    checkpoints = sorted(checkpoints,
                         key=lambda x: int(x.name.split('-')[-1]),
                         reverse=True)
    for checkpoint in checkpoints:
        if not (checkpoint / 'complete').exists():
            print(f'Removing incomplete checkpoint {checkpoint}')
            shutil.rmtree(checkpoint)
        else:
            print(f'Using checkpoint {checkpoint}, copying to ~/tmp/ for '
                  'optimization of loading.')
            tmp_dir = os.path.expanduser('~/tmp')
            os.makedirs(tmp_dir, exist_ok=True)
            try:
                # Optimization for checkpoint loading. This is to force the
                # mounting tool to download the checkpoints in parallel first.
                # It will improve the loading speed of the checkpoints
                # significantly.
                subprocess.run(
                    ['gsutil', '-m', 'rsync', '-r', checkpoint, tmp_dir],
                    check=True)
            except:
                print('Failed to optimize checkpoint loading. Skip.')
            break


def train():
    parser = transformers.HfArgumentParser((ModelArguments, TrainingArguments))
    model_args, training_args = parser.parse_args_into_dataclasses()
    local_rank = training_args.local_rank
    if local_rank == 0:
        cleanup_incomplete_checkpoints(training_args.output_dir)
    torch.distributed.barrier()

    # Check the existence of checkpoints in all processes
    # All ranks must simultaneously resume from a checkpoint if it exists.
    # Otherwise, upon recovery the model weights may not reload correctly,
    # causing loss spikes.
    resume_from_checkpoint = False
    checkpoints = list(
        pathlib.Path(training_args.output_dir).glob('checkpoint-*'))
    checkpoints = [c for c in checkpoints if c.name.split('-')[-1].isdigit()]
    if checkpoints:
        resume_from_checkpoint = True

    bnb_config = BitsAndBytesConfig(load_in_4bit=True,
                                    bnb_4bit_quant_type='nf4',
                                    bnb_4bit_compute_dtype=torch.bfloat16)

    tokenizer = AutoTokenizer.from_pretrained(model_args.model_name_or_path,
                                              token=os.environ['HF_TOKEN'])
    model = AutoModelForCausalLM.from_pretrained(model_args.model_name_or_path,
                                                 quantization_config=bnb_config,
                                                 device_map='auto',
                                                 token=os.environ['HF_TOKEN'])

    lora_config = LoraConfig(
        r=8,
        target_modules=[
            'q_proj', 'o_proj', 'k_proj', 'v_proj', 'gate_proj', 'up_proj',
            'down_proj'
        ],
        task_type='CAUSAL_LM',
    )

    data = load_dataset('Abirate/english_quotes')
    data = data.map(lambda samples: tokenizer(samples['quote']), batched=True)

    def formatting_func(example):
        text = f'Quote: {example["quote"][0]}\nAuthor: {example["author"][0]}'
        return [text]

    trainer = SFTTrainer(
        model=model,
        train_dataset=data['train'],
        args=training_args,
        peft_config=lora_config,
        formatting_func=formatting_func,
    )
    trainer.add_callback(CheckpointCallback)
    trainer.train(resume_from_checkpoint=resume_from_checkpoint)
    safe_save_model_for_hf_trainer(trainer=trainer,
                                   output_dir=training_args.output_dir)


if __name__ == "__main__":
    train()
