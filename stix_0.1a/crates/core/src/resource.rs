//! Resource management and allocation

use serde::{Deserialize, Serialize};

/// Resource type
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum Resource {
    /// CPU cores
    Cpu(f64),
    /// Memory in GB
    Memory(f64),
    /// GPU count
    Gpu(u32),
    /// Disk space in GB
    Disk(f64),
    /// Custom resource
    Custom(String, f64),
}

/// Resource requirements for a task
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct ResourceRequirements {
    /// CPU cores required
    pub cpu: Option<f64>,
    /// Memory in GB required
    pub memory: Option<f64>,
    /// Number of GPUs required
    pub gpu: Option<u32>,
    /// Disk space in GB required
    pub disk: Option<f64>,
    /// GPU type requirement
    pub gpu_type: Option<String>,
}

impl ResourceRequirements {
    /// Create new resource requirements
    pub fn new() -> Self {
        Self::default()
    }

    /// Set CPU requirement
    pub fn with_cpu(mut self, cpu: f64) -> Self {
        self.cpu = Some(cpu);
        self
    }

    /// Set memory requirement
    pub fn with_memory(mut self, memory: f64) -> Self {
        self.memory = Some(memory);
        self
    }

    /// Set GPU requirement
    pub fn with_gpu(mut self, gpu: u32) -> Self {
        self.gpu = Some(gpu);
        self
    }

    /// Set GPU type
    pub fn with_gpu_type(mut self, gpu_type: impl Into<String>) -> Self {
        self.gpu_type = Some(gpu_type.into());
        self
    }

    /// Check if requirements can be satisfied by available resources
    pub fn can_satisfy(&self, available: &ResourceRequirements) -> bool {
        if let (Some(req), Some(avail)) = (self.cpu, available.cpu) {
            if req > avail {
                return false;
            }
        }

        if let (Some(req), Some(avail)) = (self.memory, available.memory) {
            if req > avail {
                return false;
            }
        }

        if let (Some(req), Some(avail)) = (self.gpu, available.gpu) {
            if req > avail {
                return false;
            }
        }

        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_resource_requirements() {
        let req = ResourceRequirements::new()
            .with_cpu(2.0)
            .with_memory(4.0)
            .with_gpu(1);

        assert_eq!(req.cpu, Some(2.0));
        assert_eq!(req.memory, Some(4.0));
        assert_eq!(req.gpu, Some(1));
    }

    #[test]
    fn test_can_satisfy() {
        let req = ResourceRequirements::new().with_cpu(2.0).with_memory(4.0);

        let available = ResourceRequirements::new().with_cpu(4.0).with_memory(8.0);

        assert!(req.can_satisfy(&available));

        let insufficient = ResourceRequirements::new().with_cpu(1.0).with_memory(2.0);

        assert!(!req.can_satisfy(&insufficient));
    }
}
