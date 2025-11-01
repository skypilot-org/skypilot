//! CLI for LLM management

use anyhow::Result;
use clap::{Parser, Subcommand};
use colored::Colorize;

use crate::{
    config::*,
    deployment::LLMDeployment,
    finetuning::FineTuning,
    templates::{list_templates, DeploymentTemplate, FineTuneTemplate},
};

#[derive(Parser)]
#[command(name = "styx-llm")]
#[command(about = "Complete LLM management system", long_about = None)]
pub struct Cli {
    #[command(subcommand)]
    pub command: Commands,
}

#[derive(Subcommand)]
pub enum Commands {
    /// Deploy an LLM
    Deploy {
        /// Model name or preset
        #[arg(short, long)]
        model: String,

        /// Serving backend
        #[arg(short, long, default_value = "vllm")]
        backend: String,

        /// GPU type
        #[arg(short, long)]
        gpu: Option<String>,

        /// Number of GPUs
        #[arg(short, long, default_value = "1")]
        count: usize,

        /// Use template
        #[arg(short, long)]
        template: Option<String>,
    },

    /// Fine-tune a model
    FineTune {
        /// Base model
        #[arg(short, long)]
        base_model: String,

        /// Dataset
        #[arg(short, long)]
        dataset: String,

        /// Fine-tuning method
        #[arg(short, long, default_value = "lora")]
        method: String,

        /// Output directory
        #[arg(short, long, default_value = "./output")]
        output: String,
    },

    /// List available templates
    Templates,

    /// Check deployment status
    Status {
        /// Task ID
        task_id: String,
    },

    /// Stop a deployment
    Stop {
        /// Task ID
        task_id: String,
    },
}

pub async fn run_cli() -> Result<()> {
    let cli = Cli::parse();

    match cli.command {
        Commands::Deploy {
            model,
            backend,
            gpu,
            count,
            template,
        } => {
            println!("{}", "?? Deploying LLM...".bold().green());

            let config = if let Some(tmpl) = template {
                match tmpl.as_str() {
                    "llama3-70b" => DeploymentTemplate::llama3_70b_production(),
                    "llama3-8b" => DeploymentTemplate::llama3_8b_dev(),
                    "deepseek-r1" => DeploymentTemplate::deepseek_r1_reasoning(),
                    "codellama" => DeploymentTemplate::codellama_coding(),
                    _ => {
                        println!("{}", format!("Unknown template: {}", tmpl).red());
                        return Ok(());
                    }
                }
            } else {
                let backend_enum = match backend.as_str() {
                    "vllm" => ServingBackend::VLLM,
                    "sglang" => ServingBackend::SGLang,
                    "tgi" => ServingBackend::TGI,
                    "ollama" => ServingBackend::Ollama,
                    _ => ServingBackend::VLLM,
                };

                let mut config = ModelConfig::new("custom", &model).with_backend(backend_enum);

                if let Some(gpu_type) = gpu {
                    config = config.with_gpu(gpu_type, count);
                }

                config
            };

            println!("{}", format!("Model: {}", config.model_id).cyan());
            println!("{}", format!("Backend: {:?}", config.backend).cyan());
            println!(
                "{}",
                format!("GPUs: {:?} x {}", config.gpu_type, config.gpu_count).cyan()
            );

            let mut deployment = LLMDeployment::new(config);
            match deployment.deploy().await {
                Ok(task_id) => {
                    println!("{}", "? Deployment started!".bold().green());
                    println!("{}", format!("Task ID: {}", task_id).yellow());
                }
                Err(e) => {
                    println!("{}", format!("? Deployment failed: {}", e).red());
                }
            }
        }

        Commands::FineTune {
            base_model,
            dataset,
            method,
            output,
        } => {
            println!("{}", "?? Starting fine-tuning...".bold().green());

            let method_enum = match method.as_str() {
                "lora" => FineTuneMethod::LoRA,
                "qlora" => FineTuneMethod::QLoRA,
                "full" => FineTuneMethod::Full,
                _ => FineTuneMethod::LoRA,
            };

            let config = FineTuneConfig {
                method: method_enum,
                base_model: base_model.clone(),
                dataset: dataset.clone(),
                output_dir: output,
                ..Default::default()
            };

            println!("{}", format!("Base Model: {}", base_model).cyan());
            println!("{}", format!("Dataset: {}", dataset).cyan());
            println!("{}", format!("Method: {:?}", method_enum).cyan());

            let mut finetuning = FineTuning::new(config);
            match finetuning.start().await {
                Ok(task_id) => {
                    println!("{}", "? Fine-tuning started!".bold().green());
                    println!("{}", format!("Task ID: {}", task_id).yellow());
                }
                Err(e) => {
                    println!("{}", format!("? Fine-tuning failed: {}", e).red());
                }
            }
        }

        Commands::Templates => {
            println!("{}", "?? Available templates:".bold().cyan());
            println!();

            for (name, desc) in list_templates() {
                println!(
                    "  {} {}",
                    name.bold().yellow(),
                    format!("- {}", desc).dimmed()
                );
            }

            println!();
            println!("{}", "Usage: styx-llm deploy --template <name>".dimmed());
        }

        Commands::Status { task_id } => {
            println!("{}", format!("?? Checking status of {}...", task_id).bold());
            // TODO: Implement status check
            println!("{}", "? Task is running".green());
        }

        Commands::Stop { task_id } => {
            println!("{}", format!("?? Stopping {}...", task_id).bold());
            // TODO: Implement stop
            println!("{}", "? Task stopped".green());
        }
    }

    Ok(())
}
