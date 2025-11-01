//! LLM deployment orchestration

use anyhow::Result;
use async_trait::async_trait;
use styx_sky::{Task, Resources, launch};
use crate::config::*;

/// LLM deployment manager
#[derive(Debug)]
pub struct LLMDeployment {
    pub config: ModelConfig,
    pub task_id: Option<String>,
}

impl LLMDeployment {
    /// Create new deployment
    pub fn new(config: ModelConfig) -> Self {
        Self {
            config,
            task_id: None,
        }
    }
    
    /// Deploy the model
    pub async fn deploy(&mut self) -> Result<String> {
        let task = self.build_task()?;
        let task_id = launch(task, None, false).await?;
        self.task_id = Some(task_id.clone());
        Ok(task_id)
    }
    
    /// Build deployment task
    fn build_task(&self) -> Result<Task> {
        let mut task = Task::new()
            .with_name(&format!("llm-{}", self.config.name))
            .with_setup(&self.generate_setup())
            .with_run(&self.generate_run());
        
        // Add environment variables
        for (key, value) in &self.config.env_vars {
            task = task.with_env(key, value);
        }
        
        // Add resources
        let mut resources = Resources::new();
        
        if let Some(ref gpu_type) = self.config.gpu_type {
            resources = resources.with_accelerator(gpu_type, self.config.gpu_count);
        }
        
        // Set ports
        for &port in &self.config.ports {
            resources = resources.with_port(port);
        }
        
        task = task.with_resources(resources);
        
        // Enable service with replicas
        if self.config.replicas > 1 {
            task = task.with_service(self.config.replicas, None, None);
        }
        
        Ok(task)
    }
    
    /// Generate setup commands
    fn generate_setup(&self) -> String {
        match self.config.backend {
            ServingBackend::VLLM => self.setup_vllm(),
            ServingBackend::SGLang => self.setup_sglang(),
            ServingBackend::TGI => self.setup_tgi(),
            ServingBackend::Ollama => self.setup_ollama(),
            ServingBackend::LoRAX => self.setup_lorax(),
            ServingBackend::TensorRTLLM => self.setup_tensorrt(),
            ServingBackend::LocalGPT => self.setup_localgpt(),
        }
    }
    
    fn setup_vllm(&self) -> String {
        format!(r#"
# Create conda environment
conda activate vllm || conda create -n vllm python=3.10 -y
conda activate vllm

# Install vLLM
pip install vllm==0.6.3.post1
pip install flash-attn==2.5.9.post1

# Install OpenAI for API compatibility
pip install openai
"#)
    }
    
    fn setup_sglang(&self) -> String {
        format!(r#"
# Create conda environment
conda activate sglang || conda create -n sglang python=3.10 -y
conda activate sglang

# Install SGLang
pip install "sglang[all]"
pip install flashinfer -i https://flashinfer.ai/whl/cu121/torch2.4/

# Install dependencies
pip install openai anthropic
"#)
    }
    
    fn setup_tgi(&self) -> String {
        format!(r#"
# TGI uses Docker, no setup needed
echo "TGI will run via Docker"
"#)
    }
    
    fn setup_ollama(&self) -> String {
        format!(r#"
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Start Ollama service
sudo systemctl start ollama
"#)
    }
    
    fn setup_lorax(&self) -> String {
        format!(r#"
# Create conda environment
conda activate lorax || conda create -n lorax python=3.10 -y
conda activate lorax

# Install LoRAX
pip install lorax
"#)
    }
    
    fn setup_tensorrt(&self) -> String {
        format!(r#"
# Install TensorRT-LLM
pip install tensorrt_llm
"#)
    }
    
    fn setup_localgpt(&self) -> String {
        format!(r#"
# Create conda environment
conda activate localgpt || conda create -n localgpt python=3.10 -y
conda activate localgpt

# Install dependencies
pip install torch transformers accelerate bitsandbytes
pip install sentence-transformers chromadb
"#)
    }
    
    /// Generate run commands
    fn generate_run(&self) -> String {
        match self.config.backend {
            ServingBackend::VLLM => self.run_vllm(),
            ServingBackend::SGLang => self.run_sglang(),
            ServingBackend::TGI => self.run_tgi(),
            ServingBackend::Ollama => self.run_ollama(),
            ServingBackend::LoRAX => self.run_lorax(),
            ServingBackend::TensorRTLLM => self.run_tensorrt(),
            ServingBackend::LocalGPT => self.run_localgpt(),
        }
    }
    
    fn run_vllm(&self) -> String {
        let port = self.config.ports.first().copied().unwrap_or(8000);
        let mut args = vec![
            format!("--model {}", self.config.model_id),
            format!("--port {}", port),
        ];
        
        if let Some(tp) = self.config.tensor_parallel_size {
            args.push(format!("--tensor-parallel-size {}", tp));
        }
        
        if let Some(max_len) = self.config.max_model_len {
            args.push(format!("--max-model-len {}", max_len));
        }
        
        if self.config.trust_remote_code {
            args.push("--trust-remote-code".to_string());
        }
        
        if let Some(quant) = self.config.quantization {
            let quant_str = match quant {
                Quantization::AWQ4 => "awq",
                Quantization::GPTQ4 | Quantization::GPTQ8 => "gptq",
                _ => "auto",
            };
            args.push(format!("--quantization {}", quant_str));
        }
        
        format!(r#"
conda activate vllm
python -m vllm.entrypoints.openai.api_server {}
"#, args.join(" \\\n  "))
    }
    
    fn run_sglang(&self) -> String {
        let port = self.config.ports.first().copied().unwrap_or(8000);
        let mut args = vec![
            format!("--model-path {}", self.config.model_id),
            format!("--port {}", port),
        ];
        
        if let Some(tp) = self.config.tensor_parallel_size {
            args.push(format!("--tp-size {}", tp));
        }
        
        if self.config.trust_remote_code {
            args.push("--trust-remote-code".to_string());
        }
        
        format!(r#"
conda activate sglang
python -m sglang.launch_server {}
"#, args.join(" \\\n  "))
    }
    
    fn run_tgi(&self) -> String {
        let port = self.config.ports.first().copied().unwrap_or(8000);
        
        format!(r#"
docker run -d \
  --gpus all \
  -p {port}:80 \
  -v $HF_HOME:/data \
  ghcr.io/huggingface/text-generation-inference:latest \
  --model-id {} \
  --num-shard {} \
  --max-input-length 4096 \
  --max-total-tokens 8192
"#, self.config.model_id, self.config.gpu_count, port=port)
    }
    
    fn run_ollama(&self) -> String {
        format!(r#"
# Pull model
ollama pull {}

# Run model server
ollama serve
"#, self.config.model_id)
    }
    
    fn run_lorax(&self) -> String {
        let port = self.config.ports.first().copied().unwrap_or(8000);
        
        format!(r#"
docker run -d \
  --gpus all \
  -p {port}:80 \
  -v $HF_HOME:/data \
  ghcr.io/predibase/lorax:latest \
  --model-id {} \
  --num-shard {}
"#, self.config.model_id, self.config.gpu_count, port=port)
    }
    
    fn run_tensorrt(&self) -> String {
        format!(r#"
# TensorRT-LLM requires pre-built engine
python run_tensorrt.py --model {}
"#, self.config.model_id)
    }
    
    fn run_localgpt(&self) -> String {
        format!(r#"
# Run LocalGPT server
python run_localgpt.py \
  --model {} \
  --device cuda
"#, self.config.model_id)
    }
    
    /// Get deployment status
    pub async fn status(&self) -> Result<String> {
        if let Some(ref task_id) = self.task_id {
            // Use styx_sky::status()
            styx_sky::status(Some(task_id)).await?;
            Ok(format!("Task {} is running", task_id))
        } else {
            Ok("Not deployed".to_string())
        }
    }
    
    /// Stop deployment
    pub async fn stop(&self) -> Result<()> {
        if let Some(ref task_id) = self.task_id {
            styx_sky::stop(task_id, false).await?;
        }
        Ok(())
    }
    
    /// Teardown deployment
    pub async fn teardown(&self) -> Result<()> {
        if let Some(ref task_id) = self.task_id {
            styx_sky::down(task_id, false).await?;
        }
        Ok(())
    }
}

/// Deployment trait for extensibility
#[async_trait]
pub trait DeploymentStrategy {
    async fn deploy(&self, config: &ModelConfig) -> Result<String>;
    async fn status(&self, task_id: &str) -> Result<String>;
    async fn stop(&self, task_id: &str) -> Result<()>;
}

/// Cloud deployment strategy
pub struct CloudDeployment;

#[async_trait]
impl DeploymentStrategy for CloudDeployment {
    async fn deploy(&self, config: &ModelConfig) -> Result<String> {
        let mut deployment = LLMDeployment::new(config.clone());
        deployment.deploy().await
    }
    
    async fn status(&self, task_id: &str) -> Result<String> {
        styx_sky::status(Some(task_id)).await?;
        Ok(format!("Task {} status retrieved", task_id))
    }
    
    async fn stop(&self, task_id: &str) -> Result<()> {
        styx_sky::stop(task_id, false).await
    }
}

/// Local deployment strategy
pub struct LocalDeployment;

#[async_trait]
impl DeploymentStrategy for LocalDeployment {
    async fn deploy(&self, config: &ModelConfig) -> Result<String> {
        // Local deployment using Docker or Ollama
        Ok(format!("Local deployment of {}", config.model_id))
    }
    
    async fn status(&self, task_id: &str) -> Result<String> {
        Ok(format!("Local task {} is running", task_id))
    }
    
    async fn stop(&self, _task_id: &str) -> Result<()> {
        Ok(())
    }
}
