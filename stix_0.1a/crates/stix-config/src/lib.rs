//! # STIX Configuration
//!
//! Configuration management system with support for global and project-specific
//! configurations. Loads from `.stix/config.yaml` and supports environment merging.
//!
//! ## Modules
//!
//! - [`manager`] - Configuration manager
//! - [`config`] - Configuration structures
//! - [`loader`] - Configuration loader

#![warn(missing_docs)]

/// Module version
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

// Core modules
pub mod error;
pub mod config;
pub mod loader;
pub mod manager;

// Re-exports
pub use config::Config;
pub use manager::ConfigManager;
pub use loader::ConfigLoader;
pub use error::{Error, Result};

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_version() {
        assert!(!VERSION.is_empty());
    }
}
