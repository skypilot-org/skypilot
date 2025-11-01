//! Error types for Styx

use thiserror::Error;

/// Main error type for Styx
#[derive(Error, Debug)]
pub enum Error {
    #[error("Configuration error: {0}")]
    Config(String),

    #[error("Cloud provider error: {0}")]
    CloudProvider(String),

    #[error("Scheduler error: {0}")]
    Scheduler(String),

    #[error("Database error: {0}")]
    Database(String),

    #[error("Agent error: {0}")]
    Agent(String),

    #[error("Network error: {0}")]
    Network(String),

    #[error("Authentication error: {0}")]
    Auth(String),

    #[error("Resource not found: {0}")]
    NotFound(String),

    #[error("Invalid state: {0}")]
    InvalidState(String),

    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),

    #[error("Serialization error: {0}")]
    Serialization(#[from] serde_json::Error),

    #[error(transparent)]
    Other(#[from] anyhow::Error),
}

/// Result type alias
pub type Result<T> = std::result::Result<T, Error>;
