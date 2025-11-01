//! Error types for stix-auth

use thiserror::Error;

/// Result type alias for stix-auth operations
pub type Result<T> = std::result::Result<T, Error>;

/// Authentication error types
#[derive(Error, Debug)]
pub enum Error {
    /// Credentials not found
    #[error("Credentials not found for {0}")]
    CredentialsNotFound(String),

    /// Invalid credentials format
    #[error("Invalid credentials format: {0}")]
    InvalidFormat(String),

    /// Environment variable missing
    #[error("Environment variable not found: {0}")]
    EnvVarMissing(String),

    /// File not found
    #[error("Credentials file not found: {0}")]
    FileNotFound(String),

    /// IO error
    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),

    /// JSON parsing error
    #[error("JSON parsing error: {0}")]
    Json(#[from] serde_json::Error),

    /// Generic error
    #[error("{0}")]
    Generic(String),
}

impl Error {
    /// Create a credentials not found error
    pub fn credentials_not_found(provider: impl Into<String>) -> Self {
        Self::CredentialsNotFound(provider.into())
    }

    /// Create an invalid format error
    pub fn invalid_format(msg: impl Into<String>) -> Self {
        Self::InvalidFormat(msg.into())
    }

    /// Create an env var missing error
    pub fn env_var_missing(var: impl Into<String>) -> Self {
        Self::EnvVarMissing(var.into())
    }

    /// Create a file not found error
    pub fn file_not_found(path: impl Into<String>) -> Self {
        Self::FileNotFound(path.into())
    }
}
