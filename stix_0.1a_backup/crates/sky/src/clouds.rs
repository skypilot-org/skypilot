//! Clouds - Cloud provider implementations
//!
//! Python: sky/clouds/ (multiple files)
//! Rust: styx-sky/src/clouds.rs

pub mod aws;
pub mod gcp;
pub mod azure;
pub mod kubernetes;
pub mod lambda;
pub mod oci;
pub mod ibm;

use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use crate::exceptions::Result;
use crate::resources::Resources;

/// Cloud provider trait
///
/// Python: Each cloud class implements this interface
#[async_trait]
pub trait Cloud: Send + Sync {
    /// Returns cloud name
    fn name(&self) -> &str;
    
    /// Checks if cloud is enabled
    async fn is_enabled(&self) -> bool;
    
    /// Returns available regions
    async fn regions(&self) -> Result<Vec<String>>;
    
    /// Returns available zones in a region
    async fn zones(&self, region: &str) -> Result<Vec<String>>;
    
    /// Returns available instance types
    async fn instance_types(&self, region: Option<&str>) -> Result<Vec<String>>;
    
    /// Provisions a cluster
    async fn provision(
        &self,
        cluster_name: &str,
        resources: &Resources,
    ) -> Result<ClusterHandle>;
    
    /// Terminates a cluster
    async fn terminate(&self, handle: &ClusterHandle) -> Result<()>;
    
    /// Starts a stopped cluster
    async fn start(&self, handle: &ClusterHandle) -> Result<()>;
    
    /// Stops a running cluster
    async fn stop(&self, handle: &ClusterHandle) -> Result<()>;
}

/// Cluster handle for cloud operations
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ClusterHandle {
    pub cloud: String,
    pub cluster_id: String,
    pub region: Option<String>,
    pub head_ip: Option<String>,
    pub nodes: Vec<NodeInfo>,
}

/// Node information
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NodeInfo {
    pub node_id: String,
    pub instance_type: String,
    pub ip_address: Option<String>,
    pub status: NodeStatus,
}

/// Node status
#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum NodeStatus {
    Pending,
    Running,
    Stopped,
    Terminated,
}

// Re-export cloud implementations
pub use aws::AWS;
pub use azure::Azure;
pub use gcp::GCP;
pub use kubernetes::Kubernetes;
