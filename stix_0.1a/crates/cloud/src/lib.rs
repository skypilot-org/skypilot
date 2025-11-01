//! # Styx Cloud
//!
//! Multi-cloud provider abstraction layer.

pub mod aws;
pub mod gcp;
pub mod instance;
pub mod kubernetes;
pub mod provider;

pub use aws::AwsProvider;
pub use gcp::GcpProvider;
pub use instance::{Instance, InstanceId, InstanceState, InstanceType};
pub use kubernetes::KubernetesProvider;
pub use provider::{CloudProvider, ProviderType, ProvisionRequest};

/// Cloud version
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_version() {
        assert!(!VERSION.is_empty());
    }
}
