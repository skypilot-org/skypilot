//! Model serving helpers
//!
//! Provides a minimal serving abstraction that can deploy and manage long-
//! running commands on remote clusters through the `Skylet`.

use std::collections::HashMap;

use chrono::{DateTime, Utc};
use once_cell::sync::Lazy;
use tokio::sync::RwLock;

use crate::clouds::ClusterHandle;
use crate::exceptions::{Result, SkyError};
use crate::skylet::Skylet;

static SERVICES: Lazy<RwLock<HashMap<String, ServiceRecord>>> =
    Lazy::new(|| RwLock::new(HashMap::new()));

/// In-memory description of a deployed service.
#[derive(Debug, Clone)]
pub struct ServiceRecord {
    pub name: String,
    pub cluster_id: String,
    pub command: String,
    pub port: u16,
    pub pid: i64,
    pub status: String,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

/// Lightweight model serving facade.
#[derive(Debug, Clone)]
pub struct Serve {
    skylet: Skylet,
}

impl Serve {
    /// Create a new serving manager bound to a cluster.
    pub fn new(handle: ClusterHandle) -> Self {
        Self {
            skylet: Skylet::new(handle),
        }
    }

    /// Deploy a command as a background service.
    pub async fn deploy(&self, name: &str, command: &str, port: u16) -> Result<ServiceRecord> {
        let remote_cmd = format!(
            "nohup {} >/tmp/{}.log 2>&1 & echo $!",
            command,
            sanitize_name(name)
        );
        let output = self.skylet.exec(&remote_cmd).await?;

        let pid = output
            .lines()
            .last()
            .and_then(|line| line.trim().parse::<i64>().ok())
            .ok_or_else(|| SkyError::BackendError("Failed to parse remote PID".to_string()))?;

        let mut services = SERVICES.write().await;
        let record = ServiceRecord {
            name: name.to_string(),
            cluster_id: self.skylet.address().unwrap_or("local").to_string(),
            command: command.to_string(),
            port,
            pid,
            status: "running".to_string(),
            created_at: Utc::now(),
            updated_at: Utc::now(),
        };
        services.insert(name.to_string(), record.clone());
        Ok(record)
    }

    /// Stop a deployed service.
    pub async fn stop(&self, name: &str) -> Result<()> {
        let mut services = SERVICES.write().await;
        if let Some(record) = services.get_mut(name) {
            let kill_cmd = format!("kill {}", record.pid);
            let _ = self.skylet.exec(&kill_cmd).await;
            record.status = "stopped".to_string();
            record.updated_at = Utc::now();
            Ok(())
        } else {
            Err(SkyError::BackendError(format!(
                "Service '{}' not found",
                name
            )))
        }
    }

    /// Retrieve the status of a service.
    pub async fn status(&self, name: &str) -> Result<ServiceRecord> {
        let services = SERVICES.read().await;
        let record = services
            .get(name)
            .cloned()
            .ok_or_else(|| SkyError::BackendError(format!("Service '{}' not found", name)))?;

        if record.status != "running" {
            return Ok(record);
        }

        let probe_cmd = format!("kill -0 {}", record.pid);
        match self.skylet.exec(&probe_cmd).await {
            Ok(_) => Ok(record),
            Err(_) => {
                drop(services);
                let mut services = SERVICES.write().await;
                if let Some(rec) = services.get_mut(name) {
                    rec.status = "stopped".to_string();
                    rec.updated_at = Utc::now();
                    return Ok(rec.clone());
                }
                Err(SkyError::BackendError(format!(
                    "Service '{}' not found",
                    name
                )))
            }
        }
    }

    /// Tail the remote log for the service.
    pub async fn logs(&self, name: &str, lines: usize) -> Result<String> {
        let log_cmd = format!("tail -n {} /tmp/{}.log", lines, sanitize_name(name));
        self.skylet.exec(&log_cmd).await
    }

    /// List all services deployed through this manager.
    pub async fn list(&self) -> Result<Vec<ServiceRecord>> {
        let services = SERVICES.read().await;
        Ok(services.values().cloned().collect())
    }
}

fn sanitize_name(name: &str) -> String {
    name.chars()
        .filter(|c| c.is_alphanumeric() || *c == '-' || *c == '_')
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_service_lifecycle_local() {
        let handle = ClusterHandle {
            cloud: "local".to_string(),
            cluster_id: "local".to_string(),
            region: None,
            head_ip: None,
        };

        let serve = Serve::new(handle);
        let record = serve
            .deploy("sleepy", "sleep 0.01", 8080)
            .await
            .expect("deploy");
        assert_eq!(record.status, "running");

        // Service should auto-stop quickly
        tokio::time::sleep(tokio::time::Duration::from_millis(20)).await;

        serve.stop("sleepy").await.unwrap();
        let status = serve.status("sleepy").await.unwrap();
        assert_eq!(status.status, "stopped");
    }
}
