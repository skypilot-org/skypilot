//! Error types for Styx Core

use thiserror::Error;

/// Core result type
pub type Result<T> = std::result::Result<T, Error>;

/// Core error types
#[derive(Error, Debug)]
pub enum Error {
    #[error("Task error: {0}")]
    Task(String),

    #[error("Scheduler error: {0}")]
    Scheduler(String),

    #[error("Resource error: {0}")]
    Resource(String),

    #[error("Invalid state: {0}")]
    InvalidState(String),

    #[error("Not found: {0}")]
    NotFound(String),

    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),

    #[error("Serialization error: {0}")]
    Serialization(#[from] serde_json::Error),

    #[error("Internal error: {0}")]
    Internal(String),
}

impl Error {
    /// Create a task error
    pub fn task(msg: impl Into<String>) -> Self {
        Error::Task(msg.into())
    }

    /// Create a scheduler error
    pub fn scheduler(msg: impl Into<String>) -> Self {
        Error::Scheduler(msg.into())
    }

    /// Create a resource error
    pub fn resource(msg: impl Into<String>) -> Self {
        Error::Resource(msg.into())
    }
}
