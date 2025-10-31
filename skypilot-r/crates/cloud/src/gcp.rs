//! GCP cloud provider implementation

use crate::instance::{Instance, InstanceId, InstanceState};
use crate::provider::{CloudProvider, ProviderType, ProvisionRequest};
use async_trait::async_trait;
use skypilot_core::{Error, Result};
use tracing::{debug, info, warn};

/// GCP cloud provider
pub struct GcpProvider {
    project_id: String,
    zone: String,
}

impl GcpProvider {
    /// Create new GCP provider
    pub fn new(project_id: impl Into<String>, zone: impl Into<String>) -> Self {
        Self {
            project_id: project_id.into(),
            zone: zone.into(),
        }
    }

    /// Choose machine type based on requirements
    fn choose_machine_type(req: &ProvisionRequest) -> String {
        let cpu = req.resources.cpu.unwrap_or(2.0);
        let memory = req.resources.memory.unwrap_or(4.0);

        if req.resources.gpu.is_some() {
            return "n1-standard-4".to_string(); // GPU-compatible
        }

        if cpu <= 2.0 && memory <= 8.0 {
            "n1-standard-2".to_string()
        } else if cpu <= 4.0 && memory <= 16.0 {
            "n1-standard-4".to_string()
        } else if cpu <= 8.0 && memory <= 32.0 {
            "n1-standard-8".to_string()
        } else {
            "n1-standard-16".to_string()
        }
    }
}

#[async_trait]
impl CloudProvider for GcpProvider {
    fn provider_type(&self) -> ProviderType {
        ProviderType::GCP
    }

    async fn provision(&self, request: ProvisionRequest) -> Result<Instance> {
        info!("Provisioning GCP instance: {}", request.name);

        let machine_type = Self::choose_machine_type(&request);
        debug!("Selected machine type: {}", machine_type);

        // Mock instance for now
        let instance_id = InstanceId::new(format!("gcp-{}", uuid::Uuid::new_v4().simple()));
        let mut instance = Instance::new(
            instance_id,
            &request.name,
            &machine_type,
        );

        instance.region = request.region.unwrap_or_else(|| self.zone.clone());
        instance.state = InstanceState::Pending;
        instance.tags = request.tags;

        info!("GCP instance provisioned: {}", instance.id);

        Ok(instance)
    }

    async fn list_instances(&self) -> Result<Vec<Instance>> {
        debug!("Listing GCP instances in zone: {}", self.zone);
        Ok(vec![])
    }

    async fn get_instance(&self, id: &InstanceId) -> Result<Option<Instance>> {
        debug!("Getting GCP instance: {}", id);
        Ok(None)
    }

    async fn terminate(&self, id: &InstanceId) -> Result<()> {
        info!("Terminating GCP instance: {}", id);
        Ok(())
    }

    async fn start(&self, id: &InstanceId) -> Result<()> {
        info!("Starting GCP instance: {}", id);
        Ok(())
    }

    async fn stop(&self, id: &InstanceId) -> Result<()> {
        info!("Stopping GCP instance: {}", id);
        Ok(())
    }

    async fn is_available(&self) -> bool {
        // Check if GCP credentials are configured
        std::env::var("GOOGLE_APPLICATION_CREDENTIALS").is_ok()
    }

    async fn list_instance_types(&self) -> Result<Vec<String>> {
        Ok(vec![
            "n1-standard-1".to_string(),
            "n1-standard-2".to_string(),
            "n1-standard-4".to_string(),
            "n1-standard-8".to_string(),
            "n1-standard-16".to_string(),
            "n1-highmem-2".to_string(),
            "n1-highmem-4".to_string(),
            "n1-highmem-8".to_string(),
        ])
    }

    async fn list_regions(&self) -> Result<Vec<String>> {
        Ok(vec![
            "us-central1".to_string(),
            "us-east1".to_string(),
            "us-west1".to_string(),
            "europe-west1".to_string(),
            "asia-east1".to_string(),
        ])
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use skypilot_core::ResourceRequirements;

    #[test]
    fn test_choose_machine_type() {
        let req = ProvisionRequest::new(
            "test",
            ResourceRequirements::new().with_cpu(2.0).with_memory(8.0),
        );

        let machine_type = GcpProvider::choose_machine_type(&req);
        assert_eq!(machine_type, "n1-standard-2");
    }
}
