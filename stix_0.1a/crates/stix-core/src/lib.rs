//! # STIX Core
//!
//! Core orchestration engine for STIX. Provides task definition, resource management,
//! DAG execution, scheduling, and global state management.
//!
//! ## Modules
//!
//! - [`task`] - Task definition and management
//! - [`resource`] - Resource requirements and specifications
//! - [`dag`] - Directed Acyclic Graph execution
//! - [`scheduler`] - Task scheduling
//! - [`state`] - Global state management
//! - [`error`] - Error types
//! - [`config`] - Core configuration

#![warn(missing_docs)]

/// Module version
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

// Core modules
pub mod task;
pub mod resource;
pub mod dag;
pub mod scheduler;
pub mod state;
pub mod error;
pub mod config;

// Re-exports for convenience
pub use task::{Task, TaskBuilder};
pub use resource::{Resource, ResourceRequirements};
pub use dag::{Dag, DagExecutor};
pub use scheduler::Scheduler;
pub use error::{Error, Result};

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_version() {
        assert!(!VERSION.is_empty());
    }
}
