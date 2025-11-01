//! Fine-tuning orchestration

use crate::config::*;
use anyhow::Result;
use styx_sky::{launch, Resources, Task};

/// Fine-tuning manager
#[derive(Debug)]
pub struct FineTuning {
    pub config: FineTuneConfig,
    pub task_id: Option<String>,
}

impl FineTuning {
    /// Create new fine-tuning job
    pub fn new(config: FineTuneConfig) -> Self {
        Self {
            config,
            task_id: None,
        }
    }

    /// Start fine-tuning
    pub async fn start(&mut self) -> Result<String> {
        let task = self.build_task()?;
        let task_id = launch(task, None, false).await?;
        self.task_id = Some(task_id.clone());
        Ok(task_id)
    }

    fn build_task(&self) -> Result<Task> {
        let task = Task::new()
            .with_name(&format!(
                "finetune-{}",
                self.config.base_model.replace("/", "-")
            ))
            .with_setup(&self.generate_setup())
            .with_run(&self.generate_run());

        // Resources based on method
        let resources = match self.config.method {
            FineTuneMethod::Full => Resources::new().with_accelerator("A100", 8),
            FineTuneMethod::LoRA | FineTuneMethod::QLoRA => {
                Resources::new().with_accelerator("A100", 4)
            }
            _ => Resources::new().with_accelerator("A100", 2),
        };

        Ok(task.with_resources(resources))
    }

    fn generate_setup(&self) -> String {
        match self.config.method {
            FineTuneMethod::LoRA | FineTuneMethod::QLoRA => self.setup_peft(),
            FineTuneMethod::Full => self.setup_full(),
            _ => self.setup_peft(),
        }
    }

    fn setup_peft(&self) -> String {
        r#"
# Create conda environment
conda activate finetune || conda create -n finetune python=3.10 -y
conda activate finetune

# Install dependencies
pip install torch transformers accelerate peft
pip install bitsandbytes scipy
pip install datasets wandb
pip install flash-attn
"#
        .to_string()
    }

    fn setup_full(&self) -> String {
        r#"
# Create conda environment
conda activate finetune || conda create -n finetune python=3.10 -y
conda activate finetune

# Install dependencies
pip install torch transformers accelerate
pip install datasets wandb deepspeed
pip install flash-attn
"#
        .to_string()
    }

    fn generate_run(&self) -> String {
        match self.config.method {
            FineTuneMethod::LoRA => self.run_lora(),
            FineTuneMethod::QLoRA => self.run_qlora(),
            FineTuneMethod::Full => self.run_full(),
            _ => self.run_lora(),
        }
    }

    fn run_lora(&self) -> String {
        format!(
            r#"
python -c "
from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments, Trainer
from peft import LoraConfig, get_peft_model
from datasets import load_dataset

# Load model and tokenizer
model = AutoModelForCausalLM.from_pretrained('{}')
tokenizer = AutoTokenizer.from_pretrained('{}')

# LoRA config
lora_config = LoraConfig(
    r={},
    lora_alpha={},
    lora_dropout={},
    target_modules={:?},
    bias='none',
    task_type='CAUSAL_LM'
)

# Apply LoRA
model = get_peft_model(model, lora_config)
model.print_trainable_parameters()

# Load dataset
dataset = load_dataset('{}')

# Training arguments
training_args = TrainingArguments(
    output_dir='{}',
    num_train_epochs={},
    per_device_train_batch_size={},
    learning_rate={},
    logging_steps=10,
    save_steps=100,
    evaluation_strategy='steps',
    eval_steps=100,
)

# Train
trainer = Trainer(
    model=model,
    args=training_args,
    train_dataset=dataset['train'],
    eval_dataset=dataset['validation'] if 'validation' in dataset else None,
)

trainer.train()
trainer.save_model()
"
"#,
            self.config.base_model,
            self.config.base_model,
            self.config.lora_r.unwrap_or(16),
            self.config.lora_alpha.unwrap_or(32),
            self.config.lora_dropout.unwrap_or(0.05),
            self.config.target_modules,
            self.config.dataset,
            self.config.output_dir,
            self.config.epochs,
            self.config.batch_size,
            self.config.learning_rate,
        )
    }

    fn run_qlora(&self) -> String {
        format!(
            r#"
python -c "
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments, Trainer, BitsAndBytesConfig
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from datasets import load_dataset

# 4-bit quantization config
bnb_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_quant_type='nf4',
    bnb_4bit_compute_dtype=torch.float16,
    bnb_4bit_use_double_quant=True,
)

# Load model with quantization
model = AutoModelForCausalLM.from_pretrained(
    '{}',
    quantization_config=bnb_config,
    device_map='auto'
)
tokenizer = AutoTokenizer.from_pretrained('{}')

# Prepare for training
model = prepare_model_for_kbit_training(model)

# LoRA config
lora_config = LoraConfig(
    r={},
    lora_alpha={},
    lora_dropout={},
    target_modules={:?},
    bias='none',
    task_type='CAUSAL_LM'
)

# Apply LoRA
model = get_peft_model(model, lora_config)
model.print_trainable_parameters()

# Load dataset
dataset = load_dataset('{}')

# Training arguments
training_args = TrainingArguments(
    output_dir='{}',
    num_train_epochs={},
    per_device_train_batch_size={},
    learning_rate={},
    fp16=True,
    logging_steps=10,
    save_steps=100,
)

# Train
trainer = Trainer(
    model=model,
    args=training_args,
    train_dataset=dataset['train'],
)

trainer.train()
trainer.save_model()
"
"#,
            self.config.base_model,
            self.config.base_model,
            self.config.lora_r.unwrap_or(16),
            self.config.lora_alpha.unwrap_or(32),
            self.config.lora_dropout.unwrap_or(0.05),
            self.config.target_modules,
            self.config.dataset,
            self.config.output_dir,
            self.config.epochs,
            self.config.batch_size,
            self.config.learning_rate,
        )
    }

    fn run_full(&self) -> String {
        format!(
            r#"
# Full fine-tuning with DeepSpeed
deepspeed train.py \
    --model {} \
    --dataset {} \
    --output_dir {} \
    --epochs {} \
    --batch_size {} \
    --learning_rate {} \
    --deepspeed_config ds_config.json
"#,
            self.config.base_model,
            self.config.dataset,
            self.config.output_dir,
            self.config.epochs,
            self.config.batch_size,
            self.config.learning_rate,
        )
    }
}

/// Axolotl integration
pub struct AxolotlTrainer {
    config_path: String,
}

impl AxolotlTrainer {
    pub fn new(config_path: impl Into<String>) -> Self {
        Self {
            config_path: config_path.into(),
        }
    }

    pub async fn train(&self) -> Result<String> {
        let task = Task::new()
            .with_name("axolotl-train")
            .with_setup(
                r#"
# Install Axolotl
git clone https://github.com/OpenAccess-AI-Collective/axolotl
cd axolotl
pip install -e .
pip install flash-attn
"#,
            )
            .with_run(&format!(
                "cd axolotl && accelerate launch -m axolotl.cli.train {}",
                self.config_path
            ))
            .with_resources(Resources::new().with_accelerator("A100", 8));

        launch(task, None, false).await
    }
}
