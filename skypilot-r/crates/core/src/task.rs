//! Task definition and lifecycle

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use uuid::Uuid;

/// Task status
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum TaskStatus {
    Pending,
    Running,
    Completed,
    Failed,
    Cancelled,
}

/// Task priority
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub enum TaskPriority {
    Low = 0,
    Normal = 1,
    High = 2,
    Critical = 3,
}

/// A single task to be executed
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Task {
    /// Unique task ID
    pub id: Uuid,
    
    /// Task name
    pub name: String,
    
    /// Task command or script
    pub command: String,
    
    /// Environment variables
    pub env: HashMap<String, String>,
    
    /// Working directory
    pub workdir: Option<String>,
    
    /// Resource requirements
    pub resources: TaskResources,
    
    /// Task status
    pub status: TaskStatus,
    
    /// Task priority
    pub priority: TaskPriority,
    
    /// Dependencies (task IDs)
    pub dependencies: Vec<Uuid>,
    
    /// Retry configuration
    pub retry: RetryConfig,
    
    /// Timeout in seconds
    pub timeout: Option<u64>,
}

/// Task resource requirements
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TaskResources {
    /// CPU cores
    pub cpus: f32,
    
    /// Memory in GB
    pub memory_gb: f32,
    
    /// GPU count
    pub gpus: u32,
    
    /// Disk space in GB
    pub disk_gb: f32,
}

/// Retry configuration
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RetryConfig {
    /// Maximum retry attempts
    pub max_attempts: u32,
    
    /// Backoff strategy
    pub backoff: BackoffStrategy,
}

/// Backoff strategy for retries
#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum BackoffStrategy {
    Constant { delay_secs: u64 },
    Exponential { base_secs: u64, max_secs: u64 },
}

impl Task {
    /// Create a new task
    pub fn new(name: impl Into<String>, command: impl Into<String>) -> Self {
        Self {
            id: Uuid::new_v4(),
            name: name.into(),
            command: command.into(),
            env: HashMap::new(),
            workdir: None,
            resources: TaskResources::default(),
            status: TaskStatus::Pending,
            priority: TaskPriority::Normal,
            dependencies: Vec::new(),
            retry: RetryConfig::default(),
            timeout: None,
        }
    }

    /// Add a dependency
    pub fn depends_on(mut self, task_id: Uuid) -> Self {
        self.dependencies.push(task_id);
        self
    }

    /// Set priority
    pub fn with_priority(mut self, priority: TaskPriority) -> Self {
        self.priority = priority;
        self
    }

    /// Set resources
    pub fn with_resources(mut self, resources: TaskResources) -> Self {
        self.resources = resources;
        self
    }

    /// Check if task can be scheduled (all dependencies completed)
    pub fn is_ready(&self, completed_tasks: &[Uuid]) -> bool {
        self.dependencies.iter().all(|dep| completed_tasks.contains(dep))
    }
}

impl Default for TaskResources {
    fn default() -> Self {
        Self {
            cpus: 1.0,
            memory_gb: 1.0,
            gpus: 0,
            disk_gb: 10.0,
        }
    }
}

impl Default for RetryConfig {
    fn default() -> Self {
        Self {
            max_attempts: 3,
            backoff: BackoffStrategy::Exponential {
                base_secs: 1,
                max_secs: 60,
            },
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_task_creation() {
        let task = Task::new("test", "echo hello");
        assert_eq!(task.name, "test");
        assert_eq!(task.command, "echo hello");
        assert_eq!(task.status, TaskStatus::Pending);
    }

    #[test]
    fn test_task_dependencies() {
        let task1_id = Uuid::new_v4();
        let task2 = Task::new("task2", "echo world").depends_on(task1_id);
        
        assert!(!task2.is_ready(&[]));
        assert!(task2.is_ready(&[task1_id]));
    }
}
