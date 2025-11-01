//! Cloud provider implementations

pub mod aws;
pub mod gcp;
pub mod azure;
pub mod kubernetes;

use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use crate::exceptions::Result;
use crate::resources::Resources;

/// Cloud provider trait
#[async_trait]
pub trait CloudProvider: Send + Sync {
    fn name(&self) -> &str;
    async fn is_enabled(&self) -> bool;
    async fn provision(&self, name: &str, resources: &Resources) -> Result<ClusterHandle>;
    async fn terminate(&self, handle: &ClusterHandle) -> Result<()>;
}

/// Cluster handle
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ClusterHandle {
    pub cloud: String,
    pub cluster_id: String,
    pub region: Option<String>,
    pub head_ip: Option<String>,
}

// Re-exports
pub use aws::AWS;
pub use azure::Azure;
pub use gcp::GCP;
pub use kubernetes::Kubernetes;
