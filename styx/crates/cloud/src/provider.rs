//! Cloud provider trait and abstractions

use crate::instance::{Instance, InstanceId};
use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use styx_core::{Error, Result, ResourceRequirements};

/// Cloud provider type
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum ProviderType {
    /// Amazon Web Services
    AWS,
    /// Google Cloud Platform
    GCP,
    /// Microsoft Azure
    Azure,
    /// Kubernetes cluster
    Kubernetes,
    /// Local (for testing)
    Local,
}

impl std::fmt::Display for ProviderType {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ProviderType::AWS => write!(f, "aws"),
            ProviderType::GCP => write!(f, "gcp"),
            ProviderType::Azure => write!(f, "azure"),
            ProviderType::Kubernetes => write!(f, "kubernetes"),
            ProviderType::Local => write!(f, "local"),
        }
    }
}

/// Instance provisioning request
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProvisionRequest {
    /// Resource requirements
    pub resources: ResourceRequirements,
    
    /// Instance name/identifier
    pub name: String,
    
    /// Region/zone preference
    pub region: Option<String>,
    
    /// Custom tags/labels
    pub tags: std::collections::HashMap<String, String>,
    
    /// Spot/preemptible instance
    pub spot: bool,
}

impl ProvisionRequest {
    /// Create new provision request
    pub fn new(name: impl Into<String>, resources: ResourceRequirements) -> Self {
        Self {
            resources,
            name: name.into(),
            region: None,
            tags: std::collections::HashMap::new(),
            spot: false,
        }
    }

    /// Set region
    pub fn with_region(mut self, region: impl Into<String>) -> Self {
        self.region = Some(region.into());
        self
    }

    /// Add tag
    pub fn with_tag(mut self, key: impl Into<String>, value: impl Into<String>) -> Self {
        self.tags.insert(key.into(), value.into());
        self
    }

    /// Enable spot instances
    pub fn with_spot(mut self, spot: bool) -> Self {
        self.spot = spot;
        self
    }
}

/// Cloud provider trait
///
/// All cloud providers must implement this trait to integrate with Styx.
#[async_trait]
pub trait CloudProvider: Send + Sync {
    /// Get provider type
    fn provider_type(&self) -> ProviderType;

    /// Provision a new instance
    async fn provision(&self, request: ProvisionRequest) -> Result<Instance>;

    /// List all instances
    async fn list_instances(&self) -> Result<Vec<Instance>>;

    /// Get instance by ID
    async fn get_instance(&self, id: &InstanceId) -> Result<Option<Instance>>;

    /// Terminate an instance
    async fn terminate(&self, id: &InstanceId) -> Result<()>;

    /// Start a stopped instance
    async fn start(&self, id: &InstanceId) -> Result<()>;

    /// Stop a running instance
    async fn stop(&self, id: &InstanceId) -> Result<()>;

    /// Check if provider is available/configured
    async fn is_available(&self) -> bool;

    /// Get available instance types for this provider
    async fn list_instance_types(&self) -> Result<Vec<String>> {
        // Default implementation
        Ok(vec![])
    }

    /// Get available regions
    async fn list_regions(&self) -> Result<Vec<String>> {
        // Default implementation
        Ok(vec![])
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_provider_type_display() {
        assert_eq!(ProviderType::AWS.to_string(), "aws");
        assert_eq!(ProviderType::GCP.to_string(), "gcp");
        assert_eq!(ProviderType::Kubernetes.to_string(), "kubernetes");
    }

    #[test]
    fn test_provision_request() {
        let req = ProvisionRequest::new(
            "test-instance",
            ResourceRequirements::new().with_cpu(2.0).with_memory(4.0),
        )
        .with_region("us-west-2")
        .with_tag("env", "test")
        .with_spot(true);

        assert_eq!(req.name, "test-instance");
        assert_eq!(req.region, Some("us-west-2".to_string()));
        assert_eq!(req.tags.get("env"), Some(&"test".to_string()));
        assert!(req.spot);
    }
}
