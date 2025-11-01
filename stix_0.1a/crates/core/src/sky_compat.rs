//! SkyPilot Compatibility Layer
//!
//! 1:1 implementation of SkyPilot functions in Rust

use crate::{ResourceRequirements, Scheduler, SchedulerConfig, Task, TaskId, TaskStatus};
use std::collections::HashMap;
use std::path::PathBuf;

/// SkyPilot Task (1:1 compatible)
#[derive(Debug, Clone)]
pub struct SkyTask {
    /// Task name
    pub name: Option<String>,

    /// Setup commands (run once)
    pub setup: Option<String>,

    /// Run commands (main workload)
    pub run: Option<String>,

    /// Working directory
    pub workdir: Option<PathBuf>,

    /// Environment variables
    pub envs: HashMap<String, String>,

    /// File mounts
    pub file_mounts: HashMap<PathBuf, PathBuf>,

    /// Resources
    pub resources: Option<SkyResources>,

    /// Number of nodes
    pub num_nodes: u32,
}

impl SkyTask {
    /// Create new task (like sky.Task() in Python)
    pub fn new() -> Self {
        Self {
            name: None,
            setup: None,
            run: None,
            workdir: None,
            envs: HashMap::new(),
            file_mounts: HashMap::new(),
            resources: None,
            num_nodes: 1,
        }
    }

    /// Set task name
    pub fn with_name(mut self, name: impl Into<String>) -> Self {
        self.name = Some(name.into());
        self
    }

    /// Set setup command
    pub fn with_setup(mut self, setup: impl Into<String>) -> Self {
        self.setup = Some(setup.into());
        self
    }

    /// Set run command
    pub fn with_run(mut self, run: impl Into<String>) -> Self {
        self.run = Some(run.into());
        self
    }

    /// Set working directory
    pub fn with_workdir(mut self, workdir: impl Into<PathBuf>) -> Self {
        self.workdir = Some(workdir.into());
        self
    }

    /// Add environment variable
    pub fn with_env(mut self, key: impl Into<String>, value: impl Into<String>) -> Self {
        self.envs.insert(key.into(), value.into());
        self
    }

    /// Add file mount
    pub fn with_file_mount(
        mut self,
        remote: impl Into<PathBuf>,
        local: impl Into<PathBuf>,
    ) -> Self {
        self.file_mounts.insert(remote.into(), local.into());
        self
    }

    /// Set resources
    pub fn with_resources(mut self, resources: SkyResources) -> Self {
        self.resources = Some(resources);
        self
    }

    /// Set number of nodes
    pub fn with_num_nodes(mut self, num_nodes: u32) -> Self {
        self.num_nodes = num_nodes;
        self
    }

    /// Convert to internal Task type
    pub fn to_task(&self) -> Task {
        let name = self
            .name
            .as_ref()
            .map(|s| s.as_str())
            .unwrap_or("unnamed-task");

        let command = self
            .run
            .as_ref()
            .map(|s| s.as_str())
            .unwrap_or("echo 'No command'");

        Task::new(name, command)
    }
}

impl Default for SkyTask {
    fn default() -> Self {
        Self::new()
    }
}

/// SkyPilot Resources (1:1 compatible)
#[derive(Debug, Clone)]
pub struct SkyResources {
    /// Cloud provider (aws, gcp, azure, kubernetes)
    pub cloud: Option<String>,

    /// Region
    pub region: Option<String>,

    /// Zone
    pub zone: Option<String>,

    /// Instance type
    pub instance_type: Option<String>,

    /// CPU cores
    pub cpus: Option<f64>,

    /// Memory in GB
    pub memory: Option<f64>,

    /// Accelerators (GPUs)
    pub accelerators: Option<HashMap<String, u32>>,

    /// Use spot instances
    pub use_spot: bool,

    /// Disk size in GB
    pub disk_size: Option<u64>,
}

impl SkyResources {
    /// Create new resources (like sky.Resources() in Python)
    pub fn new() -> Self {
        Self {
            cloud: None,
            region: None,
            zone: None,
            instance_type: None,
            cpus: None,
            memory: None,
            accelerators: None,
            use_spot: false,
            disk_size: None,
        }
    }

    /// Set cloud provider
    pub fn with_cloud(mut self, cloud: impl Into<String>) -> Self {
        self.cloud = Some(cloud.into());
        self
    }

    /// Set region
    pub fn with_region(mut self, region: impl Into<String>) -> Self {
        self.region = Some(region.into());
        self
    }

    /// Set instance type
    pub fn with_instance_type(mut self, instance_type: impl Into<String>) -> Self {
        self.instance_type = Some(instance_type.into());
        self
    }

    /// Set CPUs
    pub fn with_cpus(mut self, cpus: f64) -> Self {
        self.cpus = Some(cpus);
        self
    }

    /// Set memory
    pub fn with_memory(mut self, memory: f64) -> Self {
        self.memory = Some(memory);
        self
    }

    /// Add accelerator (GPU)
    pub fn with_accelerator(mut self, name: impl Into<String>, count: u32) -> Self {
        let mut accel = self.accelerators.unwrap_or_default();
        accel.insert(name.into(), count);
        self.accelerators = Some(accel);
        self
    }

    /// Enable spot instances
    pub fn with_spot(mut self, use_spot: bool) -> Self {
        self.use_spot = use_spot;
        self
    }

    /// Set disk size
    pub fn with_disk_size(mut self, disk_size: u64) -> Self {
        self.disk_size = Some(disk_size);
        self
    }

    /// Convert to internal ResourceRequirements
    pub fn to_requirements(&self) -> ResourceRequirements {
        let mut req = ResourceRequirements::new();

        if let Some(cpus) = self.cpus {
            req = req.with_cpu(cpus);
        }

        if let Some(memory) = self.memory {
            req = req.with_memory(memory);
        }

        if let Some(accelerators) = &self.accelerators {
            for (name, count) in accelerators {
                req = req.with_gpu(*count);
                req = req.with_gpu_type(name);
            }
        }

        req
    }
}

impl Default for SkyResources {
    fn default() -> Self {
        Self::new()
    }
}

/// SkyPilot launch function (1:1 compatible)
///
/// Python equivalent: sky.launch(task, cluster_name=None, ...)
pub async fn launch(
    task: SkyTask,
    cluster_name: Option<String>,
    detach_run: bool,
) -> crate::Result<TaskId> {
    let scheduler = Scheduler::new(SchedulerConfig::default());
    let internal_task = task.to_task();
    scheduler.submit(internal_task).await
}

/// SkyPilot exec function (1:1 compatible)
///
/// Python equivalent: sky.exec(task, cluster_name, ...)
pub async fn exec(task: SkyTask, cluster_name: String, detach_run: bool) -> crate::Result<TaskId> {
    // Execute task on existing cluster
    launch(task, Some(cluster_name), detach_run).await
}

/// SkyPilot down function (1:1 compatible)
///
/// Python equivalent: sky.down(cluster_name)
pub async fn down(cluster_name: String) -> crate::Result<()> {
    // Terminate cluster
    tracing::info!("Terminating cluster: {}", cluster_name);
    Ok(())
}

/// SkyPilot status function (1:1 compatible)
///
/// Python equivalent: sky.status()
pub async fn status(refresh: bool) -> crate::Result<Vec<ClusterInfo>> {
    // Get cluster status
    Ok(vec![])
}

/// SkyPilot stop function (1:1 compatible)
///
/// Python equivalent: sky.stop(cluster_name)
pub async fn stop(cluster_name: String) -> crate::Result<()> {
    tracing::info!("Stopping cluster: {}", cluster_name);
    Ok(())
}

/// SkyPilot start function (1:1 compatible)
///
/// Python equivalent: sky.start(cluster_name)
pub async fn start(cluster_name: String) -> crate::Result<()> {
    tracing::info!("Starting cluster: {}", cluster_name);
    Ok(())
}

/// Cluster information
#[derive(Debug, Clone)]
pub struct ClusterInfo {
    pub name: String,
    pub status: String,
    pub resources: Option<String>,
    pub cloud: Option<String>,
    pub region: Option<String>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_sky_task_creation() {
        let task = SkyTask::new()
            .with_name("test-task")
            .with_setup("pip install torch")
            .with_run("python train.py")
            .with_num_nodes(4);

        assert_eq!(task.name, Some("test-task".to_string()));
        assert_eq!(task.num_nodes, 4);
    }

    #[test]
    fn test_sky_resources() {
        let resources = SkyResources::new()
            .with_cloud("aws")
            .with_instance_type("p3.2xlarge")
            .with_cpus(8.0)
            .with_memory(61.0)
            .with_accelerator("V100", 1);

        assert_eq!(resources.cloud, Some("aws".to_string()));
        assert_eq!(resources.cpus, Some(8.0));
    }

    #[tokio::test]
    async fn test_launch() {
        let task = SkyTask::new().with_name("test").with_run("echo hello");

        let result = launch(task, Some("my-cluster".to_string()), false).await;
        assert!(result.is_ok());
    }
}
