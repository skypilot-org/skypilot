//! Ghost - Isolated Execution Caves
//!
//! Based on agent-infra/sandbox but completely rewritten in Rust
//! 
//! # Concepts
//! - **Ghost**: The orchestrator that manages caves
//! - **Cave**: An isolated execution environment (container/pod)
//! - Supports: Docker, Docker Compose, Kubernetes
//! - Built-in secrets management
//!
//! # Architecture
//! ```text
//! Ghost
//! ??? CaveManager - Creates and manages caves
//! ??? Docker - Docker container caves
//! ??? Compose - Docker Compose caves
//! ??? Kubernetes - K8s pod caves
//! ??? Secrets - Encrypted secrets management
//! ```

pub mod cave;
pub mod ghost;
pub mod docker;
pub mod compose;
pub mod kubernetes;
pub mod secrets;
pub mod executor;

// Re-exports
pub use cave::{Cave, CaveConfig, CaveStatus, CaveType};
pub use ghost::Ghost;
pub use secrets::SecretsManager;
pub use executor::Executor;

/// Version
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_version() {
        assert!(!VERSION.is_empty());
    }
}
