//! Exceptions - Error types for Styx Sky
//!
//! Python: sky/exceptions.py (700 LoC)
//! Rust: styx-sky/src/exceptions.rs

use thiserror::Error;

/// Main error type for Styx Sky
#[derive(Error, Debug)]
pub enum SkyError {
    /// Resources unavailable
    #[error("Resources unavailable: {0}")]
    ResourcesUnavailable(String),

    /// No cloud access
    #[error("No cloud access: {0}")]
    NoCloudAccess(String),

    /// Invalid cluster name
    #[error("Invalid cluster name: {0}")]
    InvalidClusterName(String),

    /// Cluster not found
    #[error("Cluster not found: {0}")]
    ClusterNotFound(String),

    /// Cluster already exists
    #[error("Cluster already exists: {0}")]
    ClusterAlreadyExists(String),

    /// Cluster operation error
    #[error("Cluster operation error: {0}")]
    ClusterOperationError(String),

    /// Spot instance preempted
    #[error("Spot instance preempted: {0}")]
    SpotInstancePreempted(String),

    /// Spot instance not supported
    #[error("Spot instance not supported: {0}")]
    SpotInstanceNotSupported(String),

    /// Task validation error
    #[error("Task validation error: {0}")]
    TaskValidationError(String),

    /// Task execution error
    #[error("Task execution error: {0}")]
    TaskExecutionError(String),

    /// DAG validation error
    #[error("DAG validation error: {0}")]
    DagValidationError(String),

    /// Cloud provider error
    #[error("Cloud provider error ({cloud}): {message}")]
    CloudProviderError { cloud: String, message: String },

    /// Backend error
    #[error("Backend error: {0}")]
    BackendError(String),

    /// Provisioning error
    #[error("Provisioning error: {0}")]
    ProvisioningError(String),

    /// Authentication error
    #[error("Authentication error: {0}")]
    AuthenticationError(String),

    /// Configuration error
    #[error("Configuration error: {0}")]
    ConfigurationError(String),

    /// Storage error
    #[error("Storage error: {0}")]
    StorageError(String),

    /// Job error
    #[error("Job error: {0}")]
    JobError(String),

    /// Network error
    #[error("Network error: {0}")]
    NetworkError(String),

    /// Not implemented
    #[error("Not implemented: {0}")]
    NotImplemented(String),

    /// Internal error
    #[error("Internal error: {0}")]
    InternalError(String),

    /// IO error
    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),

    /// Serde error
    #[error("Serialization error: {0}")]
    Serde(#[from] serde_json::Error),

    /// Other error
    #[error("{0}")]
    Other(#[from] anyhow::Error),
}

/// Result type for Styx Sky
pub type Result<T> = std::result::Result<T, SkyError>;

// ========== Python Exception Mapping ==========

/// ResourcesUnavailableError
pub fn resources_unavailable(msg: impl Into<String>) -> SkyError {
    SkyError::ResourcesUnavailable(msg.into())
}

/// NoCloudAccessError
pub fn no_cloud_access(msg: impl Into<String>) -> SkyError {
    SkyError::NoCloudAccess(msg.into())
}

/// ClusterNotFoundError
pub fn cluster_not_found(name: impl Into<String>) -> SkyError {
    SkyError::ClusterNotFound(name.into())
}

/// ClusterAlreadyExistsError
pub fn cluster_already_exists(name: impl Into<String>) -> SkyError {
    SkyError::ClusterAlreadyExists(name.into())
}

/// SpotInstancePreemptedError
pub fn spot_preempted(msg: impl Into<String>) -> SkyError {
    SkyError::SpotInstancePreempted(msg.into())
}

/// TaskValidationError
pub fn task_validation_error(msg: impl Into<String>) -> SkyError {
    SkyError::TaskValidationError(msg.into())
}

/// DagValidationError
pub fn dag_validation_error(msg: impl Into<String>) -> SkyError {
    SkyError::DagValidationError(msg.into())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_error_types() {
        let err = resources_unavailable("test");
        assert!(err.to_string().contains("Resources unavailable"));

        let err = cluster_not_found("my-cluster");
        assert!(err.to_string().contains("my-cluster"));
    }

    #[test]
    fn test_error_conversion() {
        let io_err = std::io::Error::new(std::io::ErrorKind::NotFound, "file not found");
        let sky_err: SkyError = io_err.into();
        assert!(matches!(sky_err, SkyError::Io(_)));
    }
}
