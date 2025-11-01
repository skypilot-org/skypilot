//! # Styx Server
//!
//! REST and gRPC API server for Styx.

pub mod api;
pub mod auth;
pub mod handlers;

pub use api::ApiServer;

/// Server version
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
