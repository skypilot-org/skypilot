//! Model configuration and presets

use serde::{Deserialize, Serialize};
use std::collections::HashMap;

/// Model configuration
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelConfig {
    pub name: String,
    pub model_id: String,
    pub backend: ServingBackend,
    pub gpu_type: Option<String>,
    pub gpu_count: usize,
    pub quantization: Option<Quantization>,
    pub max_model_len: Option<usize>,
    pub tensor_parallel_size: Option<usize>,
    pub pipeline_parallel_size: Option<usize>,
    pub trust_remote_code: bool,
    pub dtype: DataType,
    pub env_vars: HashMap<String, String>,
    pub ports: Vec<u16>,
    pub replicas: usize,
}

impl Default for ModelConfig {
    fn default() -> Self {
        Self {
            name: "default".to_string(),
            model_id: "".to_string(),
            backend: ServingBackend::VLLM,
            gpu_type: None,
            gpu_count: 1,
            quantization: None,
            max_model_len: None,
            tensor_parallel_size: None,
            pipeline_parallel_size: None,
            trust_remote_code: false,
            dtype: DataType::Auto,
            env_vars: HashMap::new(),
            ports: vec![8000],
            replicas: 1,
        }
    }
}

impl ModelConfig {
    /// Create new config
    pub fn new(name: impl Into<String>, model_id: impl Into<String>) -> Self {
        Self {
            name: name.into(),
            model_id: model_id.into(),
            ..Default::default()
        }
    }
    
    /// LLaMA 3 8B preset
    pub fn llama3_8b() -> Self {
        Self::new("llama-3-8b", "meta-llama/Meta-Llama-3-8B-Instruct")
            .with_gpu("L4", 1)
    }
    
    /// LLaMA 3 70B preset
    pub fn llama3_70b() -> Self {
        Self::new("llama-3-70b", "meta-llama/Meta-Llama-3-70B-Instruct")
            .with_gpu("A100", 4)
            .with_tensor_parallel(4)
    }
    
    /// LLaMA 3.1 405B preset
    pub fn llama3_1_405b() -> Self {
        Self::new("llama-3.1-405b", "meta-llama/Meta-Llama-3.1-405B-Instruct")
            .with_gpu("A100-80GB", 8)
            .with_tensor_parallel(8)
    }
    
    /// LLaMA 4 70B preset
    pub fn llama4_70b() -> Self {
        Self::new("llama-4-70b", "meta-llama/Llama-4-70B-Instruct")
            .with_gpu("A100", 4)
            .with_tensor_parallel(4)
    }
    
    /// DeepSeek R1 preset
    pub fn deepseek_r1() -> Self {
        Self::new("deepseek-r1", "deepseek-ai/DeepSeek-R1")
            .with_gpu("A100-80GB", 8)
    }
    
    /// DeepSeek R1 Distilled preset
    pub fn deepseek_r1_distilled() -> Self {
        Self::new("deepseek-r1-distilled", "deepseek-ai/DeepSeek-R1-Distill-Llama-70B")
            .with_gpu("A100", 4)
    }
    
    /// Qwen 2.5 72B preset
    pub fn qwen2_5_72b() -> Self {
        Self::new("qwen-2.5-72b", "Qwen/Qwen2.5-72B-Instruct")
            .with_gpu("A100", 4)
    }
    
    /// Gemma 3 27B preset
    pub fn gemma3_27b() -> Self {
        Self::new("gemma-3-27b", "google/gemma-3-27b-it")
            .with_gpu("A100", 2)
    }
    
    /// Mixtral 8x7B preset
    pub fn mixtral_8x7b() -> Self {
        Self::new("mixtral-8x7b", "mistralai/Mixtral-8x7B-Instruct-v0.1")
            .with_gpu("A100", 2)
    }
    
    /// DBRX preset
    pub fn dbrx() -> Self {
        Self::new("dbrx", "databricks/dbrx-instruct")
            .with_gpu("A100-80GB", 4)
    }
    
    /// Falcon H1 preset
    pub fn falcon_h1() -> Self {
        Self::new("falcon-h1", "tiiuae/falcon-h1-180b")
            .with_gpu("A100-80GB", 8)
    }
    
    /// CodeLlama 34B preset
    pub fn codellama_34b() -> Self {
        Self::new("codellama-34b", "codellama/CodeLlama-34b-Instruct-hf")
            .with_gpu("A100", 2)
    }
    
    /// Yi 34B preset
    pub fn yi_34b() -> Self {
        Self::new("yi-34b", "01-ai/Yi-34B-Chat")
            .with_gpu("A100", 2)
    }
    
    /// Pixtral 12B vision model
    pub fn pixtral_12b() -> Self {
        Self::new("pixtral-12b", "mistralai/Pixtral-12B-2409")
            .with_gpu("A100", 1)
    }
    
    /// DeepSeek Janus vision model
    pub fn deepseek_janus() -> Self {
        Self::new("deepseek-janus", "deepseek-ai/Janus-1.3B")
            .with_gpu("L4", 1)
    }
    
    // Builder methods
    
    pub fn with_backend(mut self, backend: ServingBackend) -> Self {
        self.backend = backend;
        self
    }
    
    pub fn with_gpu(mut self, gpu_type: impl Into<String>, count: usize) -> Self {
        self.gpu_type = Some(gpu_type.into());
        self.gpu_count = count;
        self
    }
    
    pub fn with_quantization(mut self, quant: Quantization) -> Self {
        self.quantization = Some(quant);
        self
    }
    
    pub fn with_tensor_parallel(mut self, size: usize) -> Self {
        self.tensor_parallel_size = Some(size);
        self
    }
    
    pub fn with_pipeline_parallel(mut self, size: usize) -> Self {
        self.pipeline_parallel_size = Some(size);
        self
    }
    
    pub fn with_max_model_len(mut self, len: usize) -> Self {
        self.max_model_len = Some(len);
        self
    }
    
    pub fn with_dtype(mut self, dtype: DataType) -> Self {
        self.dtype = dtype;
        self
    }
    
    pub fn with_env(mut self, key: impl Into<String>, value: impl Into<String>) -> Self {
        self.env_vars.insert(key.into(), value.into());
        self
    }
    
    pub fn with_port(mut self, port: u16) -> Self {
        self.ports = vec![port];
        self
    }
    
    pub fn with_replicas(mut self, replicas: usize) -> Self {
        self.replicas = replicas;
        self
    }
    
    pub fn trust_remote_code(mut self) -> Self {
        self.trust_remote_code = true;
        self
    }
}

/// Serving backend options
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum ServingBackend {
    /// vLLM - fastest for throughput
    VLLM,
    /// SGLang - structured generation
    SGLang,
    /// TGI - Text Generation Inference
    TGI,
    /// Ollama - local serving
    Ollama,
    /// LoRAX - multi-LoRA serving
    LoRAX,
    /// TensorRT-LLM - NVIDIA optimized
    TensorRTLLM,
    /// LocalGPT - privacy-focused
    LocalGPT,
}

/// Quantization options
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Quantization {
    /// GPTQ 4-bit
    GPTQ4,
    /// GPTQ 8-bit
    GPTQ8,
    /// AWQ 4-bit
    AWQ4,
    /// GGUF Q4
    GGUFQ4,
    /// GGUF Q8
    GGUFQ8,
    /// BitsAndBytes 4-bit
    BNB4,
    /// BitsAndBytes 8-bit
    BNB8,
}

/// Data type for model weights
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum DataType {
    Auto,
    Float32,
    Float16,
    BFloat16,
    Int8,
}

/// Fine-tuning configuration
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FineTuneConfig {
    pub method: FineTuneMethod,
    pub base_model: String,
    pub dataset: String,
    pub output_dir: String,
    pub epochs: usize,
    pub batch_size: usize,
    pub learning_rate: f64,
    pub lora_r: Option<usize>,
    pub lora_alpha: Option<usize>,
    pub lora_dropout: Option<f64>,
    pub target_modules: Vec<String>,
}

impl Default for FineTuneConfig {
    fn default() -> Self {
        Self {
            method: FineTuneMethod::LoRA,
            base_model: "".to_string(),
            dataset: "".to_string(),
            output_dir: "./output".to_string(),
            epochs: 3,
            batch_size: 4,
            learning_rate: 2e-5,
            lora_r: Some(16),
            lora_alpha: Some(32),
            lora_dropout: Some(0.05),
            target_modules: vec!["q_proj".to_string(), "v_proj".to_string()],
        }
    }
}

/// Fine-tuning methods
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum FineTuneMethod {
    /// Full fine-tuning
    Full,
    /// LoRA (Low-Rank Adaptation)
    LoRA,
    /// QLoRA (Quantized LoRA)
    QLoRA,
    /// Prefix tuning
    Prefix,
    /// Prompt tuning
    Prompt,
    /// P-tuning v2
    PTuningV2,
}
