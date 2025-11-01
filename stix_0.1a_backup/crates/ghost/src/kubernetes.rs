//! Kubernetes Cave Manager
//!
//! Manages Kubernetes pod caves

use k8s_openapi::api::core::v1::{Pod, PodSpec, Container, EnvVar};
use kube::{Api, Client};
use kube::api::{PostParams, DeleteParams, LogParams};

use crate::cave::Cave;
use crate::ghost::ExecutionResult;

/// Kubernetes cave manager
pub struct KubernetesCaveManager {
    client: Option<Client>,
    namespace: String,
}

impl KubernetesCaveManager {
    /// Create new Kubernetes manager
    pub async fn new() -> anyhow::Result<Self> {
        let client = Client::try_default().await.ok();

        if client.is_some() {
            println!("??  Kubernetes: Connected");
        } else {
            println!("??  Kubernetes: Not available (skipping)");
        }

        Ok(Self {
            client,
            namespace: "default".to_string(),
        })
    }

    /// Create a Kubernetes cave
    pub async fn create_cave(&self, cave: &Cave) -> anyhow::Result<()> {
        let client = self.client.as_ref()
            .ok_or_else(|| anyhow::anyhow!("Kubernetes not available"))?;

        println!("   ??  Creating Kubernetes pod cave...");

        let pods: Api<Pod> = Api::namespaced(client.clone(), &self.namespace);

        // Prepare environment variables
        let mut env = vec![];
        for (key, value) in &cave.env {
            env.push(EnvVar {
                name: key.clone(),
                value: Some(value.clone()),
                ..Default::default()
            });
        }
        for (key, value) in &cave.secrets {
            env.push(EnvVar {
                name: key.clone(),
                value: Some(value.clone()),
                ..Default::default()
            });
        }

        // Create container spec
        let container = Container {
            name: "main".to_string(),
            image: Some(cave.config.image.clone()),
            command: cave.config.command.clone(),
            working_dir: cave.config.workdir.clone(),
            env: Some(env),
            ..Default::default()
        };

        // Create pod spec
        let pod_spec = PodSpec {
            containers: vec![container],
            restart_policy: Some("Never".to_string()),
            ..Default::default()
        };

        // Create pod
        let pod = Pod {
            metadata: k8s_openapi::apimachinery::pkg::apis::meta::v1::ObjectMeta {
                name: Some(cave.id.clone()),
                labels: Some([
                    ("app".to_string(), "ghost".to_string()),
                    ("cave".to_string(), cave.id.clone()),
                ].iter().cloned().collect()),
                ..Default::default()
            },
            spec: Some(pod_spec),
            ..Default::default()
        };

        pods.create(&PostParams::default(), &pod).await
            .map_err(|e| anyhow::anyhow!("Failed to create pod: {}", e))?;

        println!("      ? Kubernetes pod '{}' created", cave.id);

        Ok(())
    }

    /// Execute code in Kubernetes cave
    pub async fn execute(
        &self,
        cave_id: &str,
        code: &str,
        language: &str,
    ) -> anyhow::Result<ExecutionResult> {
        let client = self.client.as_ref()
            .ok_or_else(|| anyhow::anyhow!("Kubernetes not available"))?;

        let start_time = std::time::Instant::now();

        // For K8s, we'd need to use kubectl exec or the attach API
        // For now, return a placeholder
        // In production, implement proper exec via kube API

        let duration_ms = start_time.elapsed().as_millis() as u64;

        Ok(ExecutionResult {
            stdout: format!("Executed {} in K8s cave", language),
            stderr: String::new(),
            exit_code: 0,
            duration_ms,
        })
    }

    /// Stop Kubernetes cave
    pub async fn stop_cave(&self, cave_id: &str) -> anyhow::Result<()> {
        // Kubernetes pods don't stop, they terminate
        // For now, just log
        println!("   K8s pod '{}' will be terminated", cave_id);
        Ok(())
    }

    /// Destroy Kubernetes cave
    pub async fn destroy_cave(&self, cave_id: &str) -> anyhow::Result<()> {
        let client = self.client.as_ref()
            .ok_or_else(|| anyhow::anyhow!("Kubernetes not available"))?;

        let pods: Api<Pod> = Api::namespaced(client.clone(), &self.namespace);

        pods.delete(cave_id, &DeleteParams::default()).await
            .map_err(|e| anyhow::anyhow!("Failed to delete pod: {}", e))?;

        Ok(())
    }

    /// Get logs from Kubernetes cave
    pub async fn get_logs(&self, cave_id: &str) -> anyhow::Result<String> {
        let client = self.client.as_ref()
            .ok_or_else(|| anyhow::anyhow!("Kubernetes not available"))?;

        let pods: Api<Pod> = Api::namespaced(client.clone(), &self.namespace);

        let logs = pods.logs(cave_id, &LogParams::default()).await
            .map_err(|e| anyhow::anyhow!("Failed to get logs: {}", e))?;

        Ok(logs)
    }
}
