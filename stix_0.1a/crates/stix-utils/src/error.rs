//! Error types for stix-utils

use thiserror::Error;

/// Result type alias for stix-utils operations
pub type Result<T> = std::result::Result<T, Error>;

/// Utility error types
#[derive(Error, Debug)]
pub enum Error {
    /// Command execution error
    #[error("Command execution failed: {0}")]
    CommandExecution(String),

    /// Command timeout error
    #[error("Command timed out after {0} seconds")]
    CommandTimeout(u64),

    /// IO error
    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),

    /// UTF-8 conversion error
    #[error("UTF-8 conversion error: {0}")]
    Utf8(#[from] std::string::FromUtf8Error),

    /// Generic error
    #[error("{0}")]
    Generic(String),
}

impl Error {
    /// Create a command execution error
    pub fn command_execution(msg: impl Into<String>) -> Self {
        Self::CommandExecution(msg.into())
    }

    /// Create a command timeout error
    pub fn command_timeout(seconds: u64) -> Self {
        Self::CommandTimeout(seconds)
    }

    /// Create a generic error
    pub fn generic(msg: impl Into<String>) -> Self {
        Self::Generic(msg.into())
    }
}
