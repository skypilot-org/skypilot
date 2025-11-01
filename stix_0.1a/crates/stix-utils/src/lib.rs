//! # STIX Utilities
//!
//! Shared utility modules for STIX including command execution, logging,
//! networking, filesystem operations, and formatting.
//!
//! ## Modules
//!
//! - [`command`] - Command execution with Tokio
//! - [`logging`] - Logging utilities
//! - [`network`] - Network utilities
//! - [`fs`] - Filesystem utilities
//! - [`format`] - Output formatting

#![warn(missing_docs)]

/// Module version
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

// Core modules
pub mod command;
pub mod error;

// Re-exports
pub use command::{CommandRunner, CommandOutput, CommandResult};
pub use error::{Error, Result};

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_version() {
        assert!(!VERSION.is_empty());
    }
}
