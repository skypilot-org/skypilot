//! Styx Sky - Complete SkyPilot Python ? Rust Migration
//!
//! This crate is a complete 1:1 migration of SkyPilot's Python codebase to Rust.
//! It maintains API compatibility while providing the performance and safety
//! benefits of Rust.
//!
//! # Architecture
//!
//! ```text
//! styx-sky/
//! ??? core        - Core SDK/API functions (sky.launch, sky.exec, etc.)
//! ??? task        - Task definition and management
//! ??? resources   - Resource requirements and specifications
//! ??? dag         - Directed Acyclic Graph for task dependencies
//! ??? execution   - Execution engine and orchestration
//! ??? exceptions  - Error types and handling
//! ??? clouds      - Cloud provider implementations
//! ??? backends    - Backend implementations (CloudVmRay, etc.)
//! ??? adaptors    - Cloud adaptors (AWS, GCP, Azure, K8s, etc.)
//! ??? catalog     - Resource catalog and pricing
//! ??? provision   - Cluster provisioning
//! ??? skylet      - Remote agent
//! ??? serve       - Model serving
//! ??? jobs        - Job management
//! ??? data        - Storage and data management
//! ??? utils       - Utility functions
//! ```

// Core modules
pub mod core;
pub mod task;
pub mod resources;
pub mod dag;
pub mod execution;
pub mod exceptions;
pub mod state;

// Cloud and infrastructure
pub mod clouds;
pub mod backends;
pub mod adaptors;
pub mod catalog;
pub mod provision;

// Features
pub mod skylet;
pub mod serve;
pub mod jobs;
pub mod data;

// Utilities
pub mod utils;
pub mod config;

// Re-exports for convenience
pub use crate::core::*;
pub use crate::task::Task;
pub use crate::resources::Resources;
pub use crate::dag::Dag;
pub use crate::exceptions::*;

/// Version of Styx Sky
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

/// Root directory for Styx
pub fn root_dir() -> std::path::PathBuf {
    dirs::home_dir()
        .expect("Failed to get home directory")
        .join(".sky")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_version() {
        assert!(!VERSION.is_empty());
    }

    #[test]
    fn test_root_dir() {
        let dir = root_dir();
        assert!(dir.to_string_lossy().contains(".sky"));
    }
}
