//! Error types for stix-core

use thiserror::Error;

/// Result type alias for stix-core operations
pub type Result<T> = std::result::Result<T, Error>;

/// Core error types
#[derive(Error, Debug)]
pub enum Error {
    /// Task validation error
    #[error("Task validation failed: {0}")]
    TaskValidation(String),

    /// Resource validation error
    #[error("Resource validation failed: {0}")]
    ResourceValidation(String),

    /// DAG validation error
    #[error("DAG validation failed: {0}")]
    DagValidation(String),

    /// Scheduling error
    #[error("Scheduling error: {0}")]
    Scheduling(String),

    /// State management error
    #[error("State error: {0}")]
    State(String),

    /// Configuration error
    #[error("Configuration error: {0}")]
    Config(String),

    /// Serialization error
    #[error("Serialization error: {0}")]
    Serialization(#[from] serde_json::Error),

    /// IO error
    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),

    /// Generic error
    #[error("{0}")]
    Generic(String),
}

impl Error {
    /// Create a task validation error
    pub fn task_validation(msg: impl Into<String>) -> Self {
        Self::TaskValidation(msg.into())
    }

    /// Create a resource validation error
    pub fn resource_validation(msg: impl Into<String>) -> Self {
        Self::ResourceValidation(msg.into())
    }

    /// Create a DAG validation error
    pub fn dag_validation(msg: impl Into<String>) -> Self {
        Self::DagValidation(msg.into())
    }

    /// Create a scheduling error
    pub fn scheduling(msg: impl Into<String>) -> Self {
        Self::Scheduling(msg.into())
    }

    /// Create a state error
    pub fn state(msg: impl Into<String>) -> Self {
        Self::State(msg.into())
    }

    /// Create a config error
    pub fn config(msg: impl Into<String>) -> Self {
        Self::Config(msg.into())
    }

    /// Create a generic error
    pub fn generic(msg: impl Into<String>) -> Self {
        Self::Generic(msg.into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_error_creation() {
        let err = Error::task_validation("invalid task");
        assert!(err.to_string().contains("Task validation"));

        let err = Error::resource_validation("invalid resource");
        assert!(err.to_string().contains("Resource validation"));
    }
}
