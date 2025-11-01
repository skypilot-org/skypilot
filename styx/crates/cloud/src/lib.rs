//! # Styx Cloud
//!
//! Cloud provider abstractions and implementations for Styx.
//! Supports AWS, GCP, Azure, and Kubernetes.

pub mod provider;
pub mod instance;
pub mod aws;
pub mod gcp;
pub mod kubernetes;

pub use provider::{CloudProvider, ProviderType};
pub use instance::{Instance, InstanceId, InstanceState, InstanceType};

/// Cloud module version
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
