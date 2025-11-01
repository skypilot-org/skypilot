//! Cave - Isolated Execution Environment
//!
//! A Cave is a secure, isolated container where code runs

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use uuid::Uuid;

/// Cave - An isolated execution environment
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Cave {
    /// Unique cave ID
    pub id: String,
    
    /// Cave name
    pub name: String,
    
    /// Cave type (Docker, Compose, K8s)
    pub cave_type: CaveType,
    
    /// Current status
    pub status: CaveStatus,
    
    /// Configuration
    pub config: CaveConfig,
    
    /// Environment variables
    pub env: HashMap<String, String>,
    
    /// Secrets (encrypted)
    pub secrets: HashMap<String, String>,
    
    /// Created timestamp
    pub created_at: i64,
    
    /// Updated timestamp
    pub updated_at: i64,
}

/// Cave type
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub enum CaveType {
    /// Docker container
    Docker,
    
    /// Docker Compose stack
    Compose,
    
    /// Kubernetes pod
    Kubernetes,
}

/// Cave status
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub enum CaveStatus {
    /// Creating
    Creating,
    
    /// Running
    Running,
    
    /// Stopped
    Stopped,
    
    /// Failed
    Failed,
    
    /// Destroyed
    Destroyed,
}

/// Cave configuration
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CaveConfig {
    /// Container image
    pub image: String,
    
    /// Command to run
    pub command: Option<Vec<String>>,
    
    /// Working directory
    pub workdir: Option<String>,
    
    /// CPU limit (cores)
    pub cpu_limit: Option<f64>,
    
    /// Memory limit (MB)
    pub memory_limit: Option<u64>,
    
    /// Timeout (seconds)
    pub timeout: Option<u64>,
    
    /// Network mode
    pub network_mode: Option<String>,
    
    /// Volumes to mount
    pub volumes: Vec<VolumeMount>,
    
    /// Exposed ports
    pub ports: Vec<PortMapping>,
    
    /// Auto-remove after execution
    pub auto_remove: bool,
}

/// Volume mount
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VolumeMount {
    pub source: String,
    pub target: String,
    pub read_only: bool,
}

/// Port mapping
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PortMapping {
    pub host_port: u16,
    pub container_port: u16,
    pub protocol: String, // "tcp" or "udp"
}

impl Cave {
    /// Create new cave
    pub fn new(name: impl Into<String>, cave_type: CaveType, config: CaveConfig) -> Self {
        let now = chrono::Utc::now().timestamp();
        
        Self {
            id: Uuid::new_v4().to_string(),
            name: name.into(),
            cave_type,
            status: CaveStatus::Creating,
            config,
            env: HashMap::new(),
            secrets: HashMap::new(),
            created_at: now,
            updated_at: now,
        }
    }

    /// Add environment variable
    pub fn with_env(mut self, key: impl Into<String>, value: impl Into<String>) -> Self {
        self.env.insert(key.into(), value.into());
        self
    }

    /// Add secret
    pub fn with_secret(mut self, key: impl Into<String>, value: impl Into<String>) -> Self {
        self.secrets.insert(key.into(), value.into());
        self
    }

    /// Update status
    pub fn update_status(&mut self, status: CaveStatus) {
        self.status = status;
        self.updated_at = chrono::Utc::now().timestamp();
    }

    /// Check if cave is running
    pub fn is_running(&self) -> bool {
        self.status == CaveStatus::Running
    }

    /// Check if cave is stopped
    pub fn is_stopped(&self) -> bool {
        matches!(self.status, CaveStatus::Stopped | CaveStatus::Failed | CaveStatus::Destroyed)
    }
}

impl Default for CaveConfig {
    fn default() -> Self {
        Self {
            image: "ubuntu:22.04".to_string(),
            command: None,
            workdir: Some("/workspace".to_string()),
            cpu_limit: Some(1.0),
            memory_limit: Some(512),
            timeout: Some(300), // 5 minutes
            network_mode: Some("bridge".to_string()),
            volumes: vec![],
            ports: vec![],
            auto_remove: true,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_cave_creation() {
        let config = CaveConfig::default();
        let cave = Cave::new("test-cave", CaveType::Docker, config);
        
        assert_eq!(cave.name, "test-cave");
        assert_eq!(cave.cave_type, CaveType::Docker);
        assert_eq!(cave.status, CaveStatus::Creating);
    }

    #[test]
    fn test_cave_with_env() {
        let config = CaveConfig::default();
        let cave = Cave::new("test", CaveType::Docker, config)
            .with_env("KEY", "value");
        
        assert_eq!(cave.env.get("KEY"), Some(&"value".to_string()));
    }
}
