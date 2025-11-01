//! AWS cloud provider implementation

use crate::instance::{Instance, InstanceId, InstanceState};
use crate::provider::{CloudProvider, ProviderType, ProvisionRequest};
use async_trait::async_trait;
use aws_sdk_ec2::{Client as Ec2Client, types::{InstanceType as AwsInstanceType, InstanceStateName}};
use styx_core::{Error, Result};
use tracing::{debug, info, warn};

/// AWS cloud provider
pub struct AwsProvider {
    ec2_client: Ec2Client,
    region: String,
}

impl AwsProvider {
    /// Create new AWS provider
    pub async fn new() -> Result<Self> {
        let config = aws_config::load_from_env().await;
        let region = config.region()
            .map(|r| r.as_ref().to_string())
            .unwrap_or_else(|| "us-east-1".to_string());

        let ec2_client = Ec2Client::new(&config);

        Ok(Self {
            ec2_client,
            region,
        })
    }

    /// Create with specific region
    pub async fn with_region(region: impl Into<String>) -> Result<Self> {
        let region_str = region.into();
        let config = aws_config::from_env()
            .region(aws_sdk_ec2::config::Region::new(region_str.clone()))
            .load()
            .await;

        let ec2_client = Ec2Client::new(&config);

        Ok(Self {
            ec2_client,
            region: region_str,
        })
    }

    /// Map AWS instance state to our state
    fn map_state(aws_state: &InstanceStateName) -> InstanceState {
        match aws_state {
            InstanceStateName::Pending => InstanceState::Pending,
            InstanceStateName::Running => InstanceState::Running,
            InstanceStateName::Stopped | InstanceStateName::Stopping => InstanceState::Stopped,
            InstanceStateName::ShuttingDown | InstanceStateName::Terminated => {
                InstanceState::Terminated
            }
            _ => InstanceState::Error,
        }
    }

    /// Choose instance type based on requirements
    fn choose_instance_type(req: &ProvisionRequest) -> String {
        // Simple heuristic for now
        let cpu = req.resources.cpu.unwrap_or(2.0);
        let memory = req.resources.memory.unwrap_or(4.0);

        if req.resources.gpu.is_some() {
            // GPU instances
            return "p3.2xlarge".to_string();
        }

        // CPU-only instances
        if cpu <= 2.0 && memory <= 8.0 {
            "t3.medium".to_string()
        } else if cpu <= 4.0 && memory <= 16.0 {
            "t3.xlarge".to_string()
        } else if cpu <= 8.0 && memory <= 32.0 {
            "t3.2xlarge".to_string()
        } else {
            "m5.4xlarge".to_string()
        }
    }
}

#[async_trait]
impl CloudProvider for AwsProvider {
    fn provider_type(&self) -> ProviderType {
        ProviderType::AWS
    }

    async fn provision(&self, request: ProvisionRequest) -> Result<Instance> {
        info!("Provisioning AWS instance: {}", request.name);

        let instance_type = Self::choose_instance_type(&request);
        debug!("Selected instance type: {}", instance_type);

        // For now, return a mock instance
        // TODO: Actually call EC2 RunInstances
        
        let instance_id = InstanceId::new(format!("i-{}", uuid::Uuid::new_v4().simple()));
        let mut instance = Instance::new(
            instance_id,
            &request.name,
            &instance_type,
        );

        instance.region = request.region.unwrap_or_else(|| self.region.clone());
        instance.state = InstanceState::Pending;
        instance.tags = request.tags;

        info!("Instance provisioned: {}", instance.id);

        Ok(instance)
    }

    async fn list_instances(&self) -> Result<Vec<Instance>> {
        debug!("Listing AWS instances in region: {}", self.region);

        // TODO: Actually call EC2 DescribeInstances
        // For now, return empty list

        Ok(vec![])
    }

    async fn get_instance(&self, id: &InstanceId) -> Result<Option<Instance>> {
        debug!("Getting AWS instance: {}", id);

        // TODO: Call EC2 DescribeInstances with specific ID
        
        Ok(None)
    }

    async fn terminate(&self, id: &InstanceId) -> Result<()> {
        info!("Terminating AWS instance: {}", id);

        // TODO: Call EC2 TerminateInstances

        Ok(())
    }

    async fn start(&self, id: &InstanceId) -> Result<()> {
        info!("Starting AWS instance: {}", id);

        // TODO: Call EC2 StartInstances

        Ok(())
    }

    async fn stop(&self, id: &InstanceId) -> Result<()> {
        info!("Stopping AWS instance: {}", id);

        // TODO: Call EC2 StopInstances

        Ok(())
    }

    async fn is_available(&self) -> bool {
        // Try to list regions as a health check
        match self.ec2_client.describe_regions().send().await {
            Ok(_) => true,
            Err(e) => {
                warn!("AWS not available: {}", e);
                false
            }
        }
    }

    async fn list_instance_types(&self) -> Result<Vec<String>> {
        // Common AWS instance types
        Ok(vec![
            "t3.micro".to_string(),
            "t3.small".to_string(),
            "t3.medium".to_string(),
            "t3.large".to_string(),
            "t3.xlarge".to_string(),
            "t3.2xlarge".to_string(),
            "m5.large".to_string(),
            "m5.xlarge".to_string(),
            "m5.2xlarge".to_string(),
            "m5.4xlarge".to_string(),
            "p3.2xlarge".to_string(), // GPU
            "p3.8xlarge".to_string(), // GPU
        ])
    }

    async fn list_regions(&self) -> Result<Vec<String>> {
        match self.ec2_client.describe_regions().send().await {
            Ok(output) => {
                let regions = output
                    .regions()
                    .iter()
                    .filter_map(|r| r.region_name().map(|n| n.to_string()))
                    .collect();
                Ok(regions)
            }
            Err(e) => Err(Error::Internal(format!("Failed to list regions: {}", e))),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use styx_core::ResourceRequirements;

    #[test]
    fn test_choose_instance_type() {
        let req = ProvisionRequest::new(
            "test",
            ResourceRequirements::new().with_cpu(2.0).with_memory(4.0),
        );

        let instance_type = AwsProvider::choose_instance_type(&req);
        assert_eq!(instance_type, "t3.medium");

        let gpu_req = ProvisionRequest::new(
            "test-gpu",
            ResourceRequirements::new()
                .with_cpu(4.0)
                .with_memory(16.0)
                .with_gpu(1),
        );

        let gpu_type = AwsProvider::choose_instance_type(&gpu_req);
        assert_eq!(gpu_type, "p3.2xlarge");
    }
}
