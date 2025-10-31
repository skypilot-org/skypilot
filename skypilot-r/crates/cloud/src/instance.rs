//! Cloud instance model

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use uuid::Uuid;

/// Unique instance identifier
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct InstanceId(String);

impl InstanceId {
    /// Create from string
    pub fn new(id: impl Into<String>) -> Self {
        Self(id.into())
    }

    /// Generate random ID
    pub fn random() -> Self {
        Self(Uuid::new_v4().to_string())
    }

    /// Get ID as string
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl std::fmt::Display for InstanceId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl From<String> for InstanceId {
    fn from(s: String) -> Self {
        Self(s)
    }
}

impl From<&str> for InstanceId {
    fn from(s: &str) -> Self {
        Self(s.to_string())
    }
}

/// Instance state
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum InstanceState {
    /// Instance is being created
    Pending,
    /// Instance is running
    Running,
    /// Instance is stopped
    Stopped,
    /// Instance is being terminated
    Terminating,
    /// Instance has been terminated
    Terminated,
    /// Instance is in error state
    Error,
}

impl std::fmt::Display for InstanceState {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            InstanceState::Pending => write!(f, "pending"),
            InstanceState::Running => write!(f, "running"),
            InstanceState::Stopped => write!(f, "stopped"),
            InstanceState::Terminating => write!(f, "terminating"),
            InstanceState::Terminated => write!(f, "terminated"),
            InstanceState::Error => write!(f, "error"),
        }
    }
}

/// Instance type/size
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InstanceType {
    /// Type name (e.g., "t3.medium", "n1-standard-2")
    pub name: String,
    
    /// CPU cores
    pub cpu: f64,
    
    /// Memory in GB
    pub memory: f64,
    
    /// GPU count
    pub gpu: Option<u32>,
    
    /// GPU type
    pub gpu_type: Option<String>,
    
    /// Price per hour (USD)
    pub price_per_hour: Option<f64>,
}

impl InstanceType {
    /// Create new instance type
    pub fn new(name: impl Into<String>, cpu: f64, memory: f64) -> Self {
        Self {
            name: name.into(),
            cpu,
            memory,
            gpu: None,
            gpu_type: None,
            price_per_hour: None,
        }
    }
}

/// Cloud instance
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Instance {
    /// Unique instance ID
    pub id: InstanceId,
    
    /// Instance name
    pub name: String,
    
    /// Provider-specific type
    pub instance_type: String,
    
    /// Current state
    pub state: InstanceState,
    
    /// Public IP address
    pub public_ip: Option<String>,
    
    /// Private IP address
    pub private_ip: Option<String>,
    
    /// Region/zone
    pub region: String,
    
    /// Tags/labels
    pub tags: HashMap<String, String>,
    
    /// Creation timestamp
    pub created_at: chrono::DateTime<chrono::Utc>,
    
    /// Provider-specific metadata
    pub metadata: HashMap<String, String>,
}

impl Instance {
    /// Create new instance
    pub fn new(id: InstanceId, name: impl Into<String>, instance_type: impl Into<String>) -> Self {
        Self {
            id,
            name: name.into(),
            instance_type: instance_type.into(),
            state: InstanceState::Pending,
            public_ip: None,
            private_ip: None,
            region: String::new(),
            tags: HashMap::new(),
            created_at: chrono::Utc::now(),
            metadata: HashMap::new(),
        }
    }

    /// Check if instance is running
    pub fn is_running(&self) -> bool {
        self.state == InstanceState::Running
    }

    /// Check if instance is terminal (stopped or terminated)
    pub fn is_terminal(&self) -> bool {
        matches!(
            self.state,
            InstanceState::Terminated | InstanceState::Error
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_instance_id() {
        let id = InstanceId::new("i-1234567890abcdef0");
        assert_eq!(id.as_str(), "i-1234567890abcdef0");

        let random_id = InstanceId::random();
        assert!(!random_id.as_str().is_empty());
    }

    #[test]
    fn test_instance_state() {
        assert_eq!(InstanceState::Running.to_string(), "running");
        assert_eq!(InstanceState::Stopped.to_string(), "stopped");
    }

    #[test]
    fn test_instance() {
        let instance = Instance::new(
            InstanceId::new("i-test"),
            "test-instance",
            "t3.medium",
        );

        assert_eq!(instance.name, "test-instance");
        assert_eq!(instance.state, InstanceState::Pending);
        assert!(!instance.is_running());
        assert!(!instance.is_terminal());
    }
}
