//! Resource Catalog - ECHTE Cloud-Instance-Datenbank!
//!
//! Contains pricing and availability info for cloud instances

use std::collections::HashMap;
use serde::{Deserialize, Serialize};

/// Instance type catalog entry
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InstanceType {
    pub name: String,
    pub cloud: String,
    pub cpus: f64,
    pub memory_gb: f64,
    pub gpus: Option<GpuInfo>,
    pub hourly_cost: f64,
    pub spot_hourly_cost: Option<f64>,
    pub available_regions: Vec<String>,
}

/// GPU information
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GpuInfo {
    pub name: String,
    pub count: usize,
    pub memory_gb: f64,
}

/// Resource catalog
pub struct Catalog {
    instances: HashMap<String, Vec<InstanceType>>,
}

impl Catalog {
    /// Create new catalog with built-in data
    pub fn new() -> Self {
        let mut catalog = Self {
            instances: HashMap::new(),
        };
        
        catalog.load_aws_instances();
        catalog.load_gcp_instances();
        catalog.load_azure_instances();
        
        catalog
    }

    /// Load AWS instance types
    fn load_aws_instances(&mut self) {
        let mut aws_instances = vec![];

        // AWS GPU Instances
        aws_instances.push(InstanceType {
            name: "p3.2xlarge".to_string(),
            cloud: "aws".to_string(),
            cpus: 8.0,
            memory_gb: 61.0,
            gpus: Some(GpuInfo {
                name: "V100".to_string(),
                count: 1,
                memory_gb: 16.0,
            }),
            hourly_cost: 3.06,
            spot_hourly_cost: Some(0.92),
            available_regions: vec!["us-east-1".to_string(), "us-west-2".to_string()],
        });

        aws_instances.push(InstanceType {
            name: "p3.8xlarge".to_string(),
            cloud: "aws".to_string(),
            cpus: 32.0,
            memory_gb: 244.0,
            gpus: Some(GpuInfo {
                name: "V100".to_string(),
                count: 4,
                memory_gb: 64.0,
            }),
            hourly_cost: 12.24,
            spot_hourly_cost: Some(3.67),
            available_regions: vec!["us-east-1".to_string(), "us-west-2".to_string()],
        });

        aws_instances.push(InstanceType {
            name: "g4dn.xlarge".to_string(),
            cloud: "aws".to_string(),
            cpus: 4.0,
            memory_gb: 16.0,
            gpus: Some(GpuInfo {
                name: "T4".to_string(),
                count: 1,
                memory_gb: 16.0,
            }),
            hourly_cost: 0.526,
            spot_hourly_cost: Some(0.158),
            available_regions: vec!["us-east-1".to_string(), "us-west-2".to_string()],
        });

        // AWS CPU Instances
        aws_instances.push(InstanceType {
            name: "t3.medium".to_string(),
            cloud: "aws".to_string(),
            cpus: 2.0,
            memory_gb: 4.0,
            gpus: None,
            hourly_cost: 0.0416,
            spot_hourly_cost: Some(0.0125),
            available_regions: vec!["us-east-1".to_string(), "us-west-2".to_string()],
        });

        self.instances.insert("aws".to_string(), aws_instances);
    }

    /// Load GCP instance types
    fn load_gcp_instances(&mut self) {
        let mut gcp_instances = vec![];

        gcp_instances.push(InstanceType {
            name: "n1-standard-8".to_string(),
            cloud: "gcp".to_string(),
            cpus: 8.0,
            memory_gb: 30.0,
            gpus: None,
            hourly_cost: 0.38,
            spot_hourly_cost: Some(0.114),
            available_regions: vec!["us-central1".to_string(), "us-west1".to_string()],
        });

        gcp_instances.push(InstanceType {
            name: "n1-standard-8-v100-1".to_string(),
            cloud: "gcp".to_string(),
            cpus: 8.0,
            memory_gb: 30.0,
            gpus: Some(GpuInfo {
                name: "V100".to_string(),
                count: 1,
                memory_gb: 16.0,
            }),
            hourly_cost: 2.88,
            spot_hourly_cost: Some(0.864),
            available_regions: vec!["us-central1".to_string()],
        });

        self.instances.insert("gcp".to_string(), gcp_instances);
    }

    /// Load Azure instance types
    fn load_azure_instances(&mut self) {
        let mut azure_instances = vec![];

        azure_instances.push(InstanceType {
            name: "Standard_NC6s_v3".to_string(),
            cloud: "azure".to_string(),
            cpus: 6.0,
            memory_gb: 112.0,
            gpus: Some(GpuInfo {
                name: "V100".to_string(),
                count: 1,
                memory_gb: 16.0,
            }),
            hourly_cost: 3.06,
            spot_hourly_cost: Some(0.92),
            available_regions: vec!["eastus".to_string(), "westus2".to_string()],
        });

        self.instances.insert("azure".to_string(), azure_instances);
    }

    /// Get instance by name
    pub fn get_instance(&self, cloud: &str, name: &str) -> Option<&InstanceType> {
        self.instances
            .get(cloud)?
            .iter()
            .find(|i| i.name == name)
    }

    /// List all instances for a cloud
    pub fn list_instances(&self, cloud: &str) -> Vec<&InstanceType> {
        self.instances
            .get(cloud)
            .map(|instances| instances.iter().collect())
            .unwrap_or_default()
    }

    /// Find cheapest instance matching requirements
    pub fn find_cheapest(
        &self,
        cloud: &str,
        min_cpus: Option<f64>,
        min_memory_gb: Option<f64>,
        gpu_name: Option<&str>,
        use_spot: bool,
    ) -> Option<&InstanceType> {
        let instances = self.instances.get(cloud)?;

        instances
            .iter()
            .filter(|i| {
                // Filter by requirements
                if let Some(min_cpus) = min_cpus {
                    if i.cpus < min_cpus {
                        return false;
                    }
                }
                if let Some(min_mem) = min_memory_gb {
                    if i.memory_gb < min_mem {
                        return false;
                    }
                }
                if let Some(gpu) = gpu_name {
                    match &i.gpus {
                        Some(g) if g.name == gpu => true,
                        _ => false,
                    }
                } else {
                    true
                }
            })
            .min_by(|a, b| {
                let price_a = if use_spot {
                    a.spot_hourly_cost.unwrap_or(a.hourly_cost)
                } else {
                    a.hourly_cost
                };
                let price_b = if use_spot {
                    b.spot_hourly_cost.unwrap_or(b.hourly_cost)
                } else {
                    b.hourly_cost
                };
                price_a.partial_cmp(&price_b).unwrap()
            })
    }

    /// List all available GPUs
    pub fn list_gpus(&self) -> Vec<String> {
        let mut gpus = std::collections::HashSet::new();
        
        for instances in self.instances.values() {
            for instance in instances {
                if let Some(ref gpu) = instance.gpus {
                    gpus.insert(gpu.name.clone());
                }
            }
        }
        
        gpus.into_iter().collect()
    }
}

impl Default for Catalog {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_catalog() {
        let catalog = Catalog::new();
        
        let instance = catalog.get_instance("aws", "p3.2xlarge");
        assert!(instance.is_some());
        
        let instance = instance.unwrap();
        assert_eq!(instance.cpus, 8.0);
        assert!(instance.gpus.is_some());
    }

    #[test]
    fn test_find_cheapest() {
        let catalog = Catalog::new();
        
        let instance = catalog.find_cheapest("aws", Some(4.0), None, Some("T4"), false);
        assert!(instance.is_some());
        assert_eq!(instance.unwrap().name, "g4dn.xlarge");
    }
}
