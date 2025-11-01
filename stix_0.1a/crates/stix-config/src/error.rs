//! Error types for stix-config

use thiserror::Error;

/// Result type alias
pub type Result<T> = std::result::Result<T, Error>;

/// Configuration error types
#[derive(Error, Debug)]
pub enum Error {
    /// Configuration file not found
    #[error("Configuration file not found: {0}")]
    FileNotFound(String),

    /// Invalid configuration format
    #[error("Invalid configuration: {0}")]
    InvalidFormat(String),

    /// IO error
    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),

    /// YAML parsing error
    #[error("YAML parsing error: {0}")]
    Yaml(#[from] serde_yaml::Error),

    /// Generic error
    #[error("{0}")]
    Generic(String),
}
