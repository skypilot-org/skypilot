//! Resource requirements and specifications

use serde::{Deserialize, Serialize};

/// Resource requirements for task execution
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Resource {
    /// CPU cores required
    pub cpus: Option<f32>,
    
    /// Memory in GB
    pub memory: Option<f32>,
    
    /// GPU accelerators
    pub accelerators: Option<AcceleratorSpec>,
    
    /// Instance type constraints
    pub instance_type: Option<String>,
    
    /// Disk size in GB
    pub disk_size: Option<u32>,
    
    /// Disk tier (low, medium, high)
    pub disk_tier: Option<DiskTier>,
    
    /// Use spot/preemptible instances
    pub use_spot: bool,
    
    /// Cloud provider constraints
    pub cloud: Option<String>,
    
    /// Region constraints
    pub region: Option<String>,
    
    /// Zone constraints
    pub zone: Option<String>,
}

/// Accelerator specification
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AcceleratorSpec {
    /// Accelerator type (e.g., "V100", "A100", "TPU-v3")
    pub accelerator_type: String,
    
    /// Number of accelerators
    pub count: u32,
}

/// Disk tier specification
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub enum DiskTier {
    /// Low performance (HDD)
    Low,
    /// Medium performance (Standard SSD)
    Medium,
    /// High performance (NVMe SSD)
    High,
}

/// Resource requirements builder
#[derive(Debug, Default)]
pub struct ResourceRequirements {
    cpus: Option<f32>,
    memory: Option<f32>,
    accelerators: Option<AcceleratorSpec>,
    instance_type: Option<String>,
    disk_size: Option<u32>,
    disk_tier: Option<DiskTier>,
    use_spot: bool,
    cloud: Option<String>,
    region: Option<String>,
    zone: Option<String>,
}

impl ResourceRequirements {
    /// Create new resource requirements
    pub fn new() -> Self {
        Self::default()
    }

    /// Set CPU requirements
    pub fn cpus(mut self, cpus: f32) -> Self {
        self.cpus = Some(cpus);
        self
    }

    /// Set memory requirements in GB
    pub fn memory(mut self, memory: f32) -> Self {
        self.memory = Some(memory);
        self
    }

    /// Set accelerator requirements
    pub fn accelerators(mut self, accelerator_type: impl Into<String>, count: u32) -> Self {
        self.accelerators = Some(AcceleratorSpec {
            accelerator_type: accelerator_type.into(),
            count,
        });
        self
    }

    /// Set instance type
    pub fn instance_type(mut self, instance_type: impl Into<String>) -> Self {
        self.instance_type = Some(instance_type.into());
        self
    }

    /// Set disk size in GB
    pub fn disk_size(mut self, size: u32) -> Self {
        self.disk_size = Some(size);
        self
    }

    /// Set disk tier
    pub fn disk_tier(mut self, tier: DiskTier) -> Self {
        self.disk_tier = Some(tier);
        self
    }

    /// Enable spot/preemptible instances
    pub fn use_spot(mut self) -> Self {
        self.use_spot = true;
        self
    }

    /// Set cloud provider
    pub fn cloud(mut self, cloud: impl Into<String>) -> Self {
        self.cloud = Some(cloud.into());
        self
    }

    /// Set region
    pub fn region(mut self, region: impl Into<String>) -> Self {
        self.region = Some(region.into());
        self
    }

    /// Set zone
    pub fn zone(mut self, zone: impl Into<String>) -> Self {
        self.zone = Some(zone.into());
        self
    }

    /// Build the resource specification
    pub fn build(self) -> Resource {
        Resource {
            cpus: self.cpus,
            memory: self.memory,
            accelerators: self.accelerators,
            instance_type: self.instance_type,
            disk_size: self.disk_size,
            disk_tier: self.disk_tier,
            use_spot: self.use_spot,
            cloud: self.cloud,
            region: self.region,
            zone: self.zone,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_resource_builder() {
        let resource = ResourceRequirements::new()
            .cpus(4.0)
            .memory(16.0)
            .accelerators("V100", 2)
            .use_spot()
            .cloud("aws")
            .region("us-west-2")
            .build();

        assert_eq!(resource.cpus, Some(4.0));
        assert_eq!(resource.memory, Some(16.0));
        assert!(resource.use_spot);
        assert_eq!(resource.cloud, Some("aws".to_string()));
    }
}
