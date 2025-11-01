//! # Styx LLM - Complete LLM Management System
//!
//! Comprehensive Rust implementation of LLM deployment, serving, and fine-tuning.
//!
//! ## Features
//! - LLM Serving (vLLM, SGLang, TGI, Ollama)
//! - Fine-tuning (Axolotl, LoRA, QLoRA)
//! - Batch Inference
//! - RAG Systems
//! - Chat Interfaces
//! - RL Training
//!
//! ## Example
//! ```rust,no_run
//! use styx_llm::{LLMDeployment, ModelConfig, ServingBackend};
//!
//! #[tokio::main]
//! async fn main() -> anyhow::Result<()> {
//!     let config = ModelConfig::llama3_70b()
//!         .with_backend(ServingBackend::VLLM)
//!         .with_gpu("A100", 4);
//!     
//!     let deployment = LLMDeployment::new(config);
//!     deployment.deploy().await?;
//!     
//!     Ok(())
//! }
//! ```

// Core modules
pub mod chat;
pub mod cli;
pub mod config;
pub mod deployment;
pub mod finetuning;
pub mod inference;
pub mod rag;
pub mod rl;
pub mod serving;
pub mod templates;

// Re-exports
pub use config::*;
pub use deployment::*;
pub use finetuning::*;
pub use serving::*;

/// LLM Error types
#[derive(Debug, thiserror::Error)]
pub enum LLMError {
    #[error("Deployment error: {0}")]
    DeploymentError(String),

    #[error("Model not found: {0}")]
    ModelNotFound(String),

    #[error("Inference error: {0}")]
    InferenceError(String),

    #[error("Training error: {0}")]
    TrainingError(String),

    #[error("Configuration error: {0}")]
    ConfigError(String),

    #[error("Backend error: {0}")]
    BackendError(String),

    #[error(transparent)]
    Other(#[from] anyhow::Error),
}

pub type Result<T> = std::result::Result<T, LLMError>;
