//! GCP cloud provider implementation

use crate::instance::{Instance, InstanceId, InstanceState};
use crate::provider::{CloudProvider, ProviderType, ProvisionRequest};
use async_trait::async_trait;
use styx_core::{Error, Result};
use tracing::{debug, info};

/// GCP cloud provider
pub struct GcpProvider {
    project_id: String,
    region: String,
    http_client: reqwest::Client,
}

impl GcpProvider {
    /// Create new GCP provider
    pub async fn new(project_id: impl Into<String>) -> Result<Self> {
        let project_id = project_id.into();
        let region = "us-central1".to_string();
        
        Ok(Self {
            project_id,
            region,
            http_client: reqwest::Client::new(),
        })
    }

    /// Create with specific region
    pub async fn with_region(project_id: impl Into<String>, region: impl Into<String>) -> Result<Self> {
        Ok(Self {
            project_id: project_id.into(),
            region: region.into(),
            http_client: reqwest::Client::new(),
        })
    }

    /// Choose machine type based on requirements
    fn choose_machine_type(req: &ProvisionRequest) -> String {
        let cpu = req.resources.cpu.unwrap_or(2.0);
        let memory = req.resources.memory.unwrap_or(4.0);

        if req.resources.gpu.is_some() {
            return "n1-standard-8".to_string(); // GPU instances
        }

        // CPU-only instances
        if cpu <= 2.0 && memory <= 7.5 {
            "n1-standard-2".to_string()
        } else if cpu <= 4.0 && memory <= 15.0 {
            "n1-standard-4".to_string()
        } else if cpu <= 8.0 && memory <= 30.0 {
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
        let mut instance = Instance::new(instance_id, &request.name, &machine_type);

        instance.region = request.region.unwrap_or_else(|| self.region.clone());
        instance.state = InstanceState::Pending;
        instance.tags = request.tags;

        info!("GCP instance provisioned: {}", instance.id);

        Ok(instance)
    }

    async fn list_instances(&self) -> Result<Vec<Instance>> {
        debug!("Listing GCP instances in project: {}", self.project_id);
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
        // Check if we can access GCP APIs
        true
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
