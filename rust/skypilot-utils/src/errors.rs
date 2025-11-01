//! Error types and conversions for SkyPilot Rust utilities

use pyo3::exceptions::PyException;
use pyo3::prelude::*;
use std::fmt;

/// Custom error type for SkyPilot Rust operations
#[derive(Debug)]
pub enum SkyPilotError {
    IoError(std::io::Error),
    ParseError(String),
    SystemError(String),
    NotFound(String),
}

impl fmt::Display for SkyPilotError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            SkyPilotError::IoError(e) => write!(f, "I/O error: {}", e),
            SkyPilotError::ParseError(msg) => write!(f, "Parse error: {}", msg),
            SkyPilotError::SystemError(msg) => write!(f, "System error: {}", msg),
            SkyPilotError::NotFound(msg) => write!(f, "Not found: {}", msg),
        }
    }
}

impl std::error::Error for SkyPilotError {}

impl From<std::io::Error> for SkyPilotError {
    fn from(err: std::io::Error) -> Self {
        SkyPilotError::IoError(err)
    }
}

impl From<SkyPilotError> for PyErr {
    fn from(err: SkyPilotError) -> Self {
        PyException::new_err(err.to_string())
    }
}

/// Result type alias for SkyPilot operations
pub type Result<T> = std::result::Result<T, SkyPilotError>;
