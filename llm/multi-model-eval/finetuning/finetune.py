#!/usr/bin/env python3
"""
Example finetuning script that saves model to S3 bucket or SkyPilot volume.

This script demonstrates:
1. Loading a base model from HuggingFace
2. Fine-tuning on a sample dataset
3. Saving to either S3 bucket or SkyPilot volume based on OUTPUT_PATH env var
"""

import logging
import os
import time

from datasets import load_dataset
import torch
from transformers import AutoModelForCausalLM
from transformers import AutoTokenizer
from transformers import DataCollatorForLanguageModeling
from transformers import Trainer
from transformers import TrainingArguments

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def prepare_dataset(tokenizer, max_length=512):
    """Prepare a simple dataset for demonstration."""
    # Load a small dataset for demo
    dataset = load_dataset("imdb", split="train[:1000]")

    def tokenize_function(examples):
        return tokenizer(
            examples["text"],
            truncation=True,
            padding="max_length",
            max_length=max_length,
        )

    tokenized_dataset = dataset.map(tokenize_function, batched=True)
    return tokenized_dataset


def main():
    # Get configuration from environment
    model_name = os.environ.get("BASE_MODEL", "gpt2")
    output_path = os.environ.get("OUTPUT_PATH", "./finetuned-model")
    num_epochs = int(os.environ.get("NUM_EPOCHS", "1"))

    logger.info(f"Loading base model: {model_name}")
    logger.info(f"Output path: {output_path}")

    # Load model and tokenizer
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForCausalLM.from_pretrained(model_name)

    # Add padding token if missing
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # Prepare dataset
    logger.info("Preparing dataset...")
    train_dataset = prepare_dataset(tokenizer)

    # Set up training arguments
    training_args = TrainingArguments(
        output_dir="./training-checkpoints",
        overwrite_output_dir=True,
        num_train_epochs=num_epochs,
        per_device_train_batch_size=4,
        per_device_eval_batch_size=4,
        warmup_steps=100,
        logging_steps=10,
        save_strategy="epoch",
        evaluation_strategy="no",
        learning_rate=5e-5,
        fp16=torch.cuda.is_available(),
        gradient_checkpointing=True,
        report_to=[]  # Disable wandb/tensorboard for demo
    )

    # Create data collator
    data_collator = DataCollatorForLanguageModeling(
        tokenizer=tokenizer,
        mlm=False,
    )

    # Initialize trainer
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        data_collator=data_collator,
    )

    # Train
    logger.info("Starting training...")
    trainer.train()

    # Save the final model
    logger.info(f"Saving model to {output_path}")

    # Save model directly to the output path
    # Since buckets and volumes are already mounted by SkyPilot,
    # we can save directly without using AWS CLI
    logger.info(f"Saving model to {output_path}")
    trainer.save_model(output_path)
    tokenizer.save_pretrained(output_path)
    
    # Log the save location type for clarity
    if output_path.startswith("/buckets/"):
        logger.info("Model saved to S3/GCS bucket (mounted)")
    elif output_path.startswith("/volumes/"):
        logger.info("Model saved to SkyPilot volume")
    else:
        logger.info("Model saved to local disk")

    logger.info("Training complete!")

    # Create a marker file to indicate completion
    marker_path = os.path.join(output_path, "training_complete.txt")
    with open(marker_path, "w", encoding="utf-8") as f:
        f.write("Training completed successfully\n")
        f.write(f"Model: {model_name}\n")
        f.write(f"Output: {output_path}\n")
        f.write(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n")


if __name__ == "__main__":
    main()
