//! # STIX Authentication
//!
//! Credential management for cloud providers including AWS, GCP, and Azure.
//! Supports environment-based and file-based credential discovery with caching.
//!
//! ## Modules
//!
//! - [`credentials`] - Credential structures
//! - [`providers`] - Cloud provider credential detection
//! - [`manager`] - Credential manager with caching

#![warn(missing_docs)]

/// Module version
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

// Core modules
pub mod error;
pub mod credentials;
pub mod providers;
pub mod manager;

// Re-exports
pub use credentials::{Credentials, CloudProvider};
pub use manager::CredentialManager;
pub use error::{Error, Result};

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_version() {
        assert!(!VERSION.is_empty());
    }
}
