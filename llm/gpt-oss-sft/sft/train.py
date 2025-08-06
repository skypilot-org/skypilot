import argparse
import os

from accelerate import Accelerator
from accelerate import ProfileKwargs
from datasets import load_dataset
from peft import get_peft_model
from peft import LoraConfig
from transformers import AutoModelForCausalLM
from transformers import AutoTokenizer
from transformers import Mxfp4Config
from trl import SFTConfig
from trl import SFTTrainer


class ProfilingSFTTrainer(SFTTrainer):

    def __init__(self, *args, accelerator_profiler=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.accelerator_profiler = accelerator_profiler

    def training_step(self, *args, **kwargs):
        result = super().training_step(*args, **kwargs)
        if self.accelerator_profiler is not None:
            self.accelerator_profiler.step()
        return result


def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="Train a model using SFT on Codeforces dataset")
    parser.add_argument(
        "--model_id",
        type=str,
        default="openai/gpt-oss-20b",
        help="The model ID to use for training (default: openai/gpt-oss-120b)")
    parser.add_argument("--enable_lora",
                        action="store_true",
                        default=False,
                        help="Enable LoRA")
    parser.add_argument(
        "--enable_profiling",
        action="store_true",
        default=False,
        help="Enable accelerate profiling with chrome trace export")
    args = parser.parse_args()

    # Setup profiling if enabled
    accelerator_kwargs = {}
    if args.enable_profiling:

        def trace_handler(p):
            p.export_chrome_trace(f"/tmp/trace_{p.step_num}.json")

        profile_kwargs = ProfileKwargs(activities=["cpu", "cuda"],
                                       schedule_option={
                                           "wait": 0,
                                           "warmup": 1,
                                           "active": 3,
                                           "repeat": 0,
                                           "skip_first": 1
                                       },
                                       on_trace_ready=trace_handler)
        accelerator_kwargs['kwargs_handlers'] = [profile_kwargs]

    accelerator = Accelerator(**accelerator_kwargs)
    model_id = args.model_id

    # Load dataset
    num_proc = int(os.cpu_count() / 2)
    train_dataset = load_dataset("HuggingFaceH4/Multilingual-Thinking",
                                 split="train",
                                 num_proc=num_proc)

    quantization_config = Mxfp4Config(dequantize=True)

    # Load model
    model = AutoModelForCausalLM.from_pretrained(
        model_id,
        attn_implementation="eager",
        torch_dtype="auto",
        use_cache=False,
        quantization_config=quantization_config,
    )

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
            target_modules="all-linear",
            target_parameters=target_parameters,
        )
        model = get_peft_model(model, peft_config)
        model.print_trainable_parameters()

    per_device_train_batch_size = 1
    if not args.enable_lora:
        if args.model_id == 'openai/gpt-oss-120b':
            per_device_train_batch_size = 4
        if args.model_id == 'openai/gpt-oss-20b':
            per_device_train_batch_size = 1
    else:
        per_device_train_batch_size = 1

    # Train model
    training_args = SFTConfig(
        output_dir=f"{model_id}-checkpoint",
        learning_rate=2e-4,
        num_train_epochs=1,
        logging_steps=1,
        per_device_train_batch_size=per_device_train_batch_size,
        gradient_accumulation_steps=1,
        max_length=1024,
        warmup_ratio=0.03,
        lr_scheduler_type="cosine_with_min_lr",
        lr_scheduler_kwargs={"min_lr_rate": 0.1},
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
    else:
        trainer = ProfilingSFTTrainer(**trainer_kwargs)
        trainer.train()


if __name__ == "__main__":
    main()
