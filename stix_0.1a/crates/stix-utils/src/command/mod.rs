//! Command execution utilities
//!
//! Provides async command execution with Tokio, capturing stdout/stderr,
//! and structured error handling.

mod runner;
mod output;

pub use runner::CommandRunner;
pub use output::{CommandOutput, CommandResult};
