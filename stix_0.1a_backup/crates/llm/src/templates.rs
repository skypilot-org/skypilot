//! Pre-configured deployment templates

use crate::config::*;
use crate::deployment::LLMDeployment;
use anyhow::Result;

/// Template for popular LLM deployments
pub struct DeploymentTemplate;

impl DeploymentTemplate {
    /// LLaMA 3 70B with vLLM (recommended for production)
    pub fn llama3_70b_production() -> ModelConfig {
        ModelConfig::llama3_70b()
            .with_backend(ServingBackend::VLLM)
            .with_quantization(Quantization::AWQ4)
            .with_replicas(2)
            .trust_remote_code()
    }

    /// LLaMA 3 8B for development
    pub fn llama3_8b_dev() -> ModelConfig {
        ModelConfig::llama3_8b()
            .with_backend(ServingBackend::VLLM)
            .with_gpu("L4", 1)
    }

    /// DeepSeek R1 for reasoning
    pub fn deepseek_r1_reasoning() -> ModelConfig {
        ModelConfig::deepseek_r1()
            .with_backend(ServingBackend::VLLM)
            .with_tensor_parallel(8)
            .with_max_model_len(32768)
    }

    /// Code generation with CodeLlama
    pub fn codellama_coding() -> ModelConfig {
        ModelConfig::codellama_34b()
            .with_backend(ServingBackend::VLLM)
            .with_env("CODE_MODE", "true")
    }

    /// Vision model (Pixtral)
    pub fn pixtral_vision() -> ModelConfig {
        ModelConfig::pixtral_12b()
            .with_backend(ServingBackend::VLLM)
            .trust_remote_code()
    }

    /// Multi-LoRA serving
    pub fn multi_lora_serving(base_model: &str) -> ModelConfig {
        ModelConfig::new("multi-lora", base_model)
            .with_backend(ServingBackend::LoRAX)
            .with_gpu("A100", 4)
    }

    /// Local deployment with Ollama
    pub fn local_ollama(model: &str) -> ModelConfig {
        ModelConfig::new("local", model)
            .with_backend(ServingBackend::Ollama)
            .with_gpu("RTX4090", 1)
    }

    /// SGLang for structured generation
    pub fn sglang_structured(model: &str) -> ModelConfig {
        ModelConfig::new("sglang", model)
            .with_backend(ServingBackend::SGLang)
            .with_gpu("A100", 2)
    }
}

/// Fine-tuning templates
pub struct FineTuneTemplate;

impl FineTuneTemplate {
    /// LoRA fine-tuning
    pub fn lora_default(base_model: &str, dataset: &str) -> FineTuneConfig {
        FineTuneConfig {
            method: FineTuneMethod::LoRA,
            base_model: base_model.to_string(),
            dataset: dataset.to_string(),
            lora_r: Some(16),
            lora_alpha: Some(32),
            lora_dropout: Some(0.05),
            ..Default::default()
        }
    }

    /// QLoRA for efficient fine-tuning
    pub fn qlora_efficient(base_model: &str, dataset: &str) -> FineTuneConfig {
        FineTuneConfig {
            method: FineTuneMethod::QLoRA,
            base_model: base_model.to_string(),
            dataset: dataset.to_string(),
            lora_r: Some(32),
            lora_alpha: Some(64),
            ..Default::default()
        }
    }

    /// Full fine-tuning for maximum quality
    pub fn full_finetune(base_model: &str, dataset: &str) -> FineTuneConfig {
        FineTuneConfig {
            method: FineTuneMethod::Full,
            base_model: base_model.to_string(),
            dataset: dataset.to_string(),
            epochs: 3,
            batch_size: 1,
            ..Default::default()
        }
    }
}

/// Quick deploy functions
pub async fn quick_deploy(template_name: &str) -> Result<String> {
    let config = match template_name {
        "llama3-70b" => DeploymentTemplate::llama3_70b_production(),
        "llama3-8b" => DeploymentTemplate::llama3_8b_dev(),
        "deepseek-r1" => DeploymentTemplate::deepseek_r1_reasoning(),
        "codellama" => DeploymentTemplate::codellama_coding(),
        "pixtral" => DeploymentTemplate::pixtral_vision(),
        _ => anyhow::bail!("Unknown template: {}", template_name),
    };

    let mut deployment = LLMDeployment::new(config);
    deployment.deploy().await
}

/// List all available templates
pub fn list_templates() -> Vec<(&'static str, &'static str)> {
    vec![
        ("llama3-70b", "LLaMA 3 70B with vLLM (production)"),
        ("llama3-8b", "LLaMA 3 8B for development"),
        ("deepseek-r1", "DeepSeek R1 for reasoning"),
        ("codellama", "CodeLlama for code generation"),
        ("pixtral", "Pixtral vision model"),
        ("mixtral", "Mixtral 8x7B MoE"),
        ("qwen", "Qwen 2.5 72B"),
        ("gemma3", "Gemma 3 27B"),
    ]
}
