//! Core task definition

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::time::Duration;

/// Unique task identifier
pub type TaskId = String;

/// Task execution status
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum TaskStatus {
    /// Task is pending execution
    Pending,
    /// Task is currently running
    Running,
    /// Task completed successfully
    Success,
    /// Task failed
    Failed,
    /// Task was cancelled
    Cancelled,
    /// Task is being retried
    Retrying,
}

/// Retry policy for failed tasks
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RetryPolicy {
    /// Maximum number of retry attempts
    pub max_attempts: u32,
    /// Delay between retries
    pub retry_delay: Duration,
    /// Exponential backoff multiplier
    pub backoff_multiplier: f64,
    /// Maximum retry delay
    pub max_retry_delay: Duration,
}

impl Default for RetryPolicy {
    fn default() -> Self {
        Self {
            max_attempts: 3,
            retry_delay: Duration::from_secs(10),
            backoff_multiplier: 2.0,
            max_retry_delay: Duration::from_secs(300),
        }
    }
}

/// Main task definition
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Task {
    /// Unique identifier
    pub id: TaskId,
    
    /// Human-readable name
    pub name: String,
    
    /// Task description
    pub description: Option<String>,
    
    /// Command or script to execute
    pub run: String,
    
    /// Setup commands (run before main task)
    pub setup: Option<String>,
    
    /// Working directory
    pub workdir: Option<String>,
    
    /// Environment variables
    pub env: HashMap<String, String>,
    
    /// Task dependencies (IDs of tasks that must complete first)
    pub dependencies: Vec<TaskId>,
    
    /// Retry policy
    pub retry_policy: Option<RetryPolicy>,
    
    /// Timeout duration
    pub timeout: Option<Duration>,
    
    /// Current status
    pub status: TaskStatus,
    
    /// Number of attempts made
    pub attempts: u32,
    
    /// Additional metadata
    pub metadata: HashMap<String, String>,
}

impl Task {
    /// Create a new task with minimal configuration
    pub fn new(name: impl Into<String>, run: impl Into<String>) -> Self {
        Self {
            id: uuid::Uuid::new_v4().to_string(),
            name: name.into(),
            description: None,
            run: run.into(),
            setup: None,
            workdir: None,
            env: HashMap::new(),
            dependencies: Vec::new(),
            retry_policy: None,
            timeout: None,
            status: TaskStatus::Pending,
            attempts: 0,
            metadata: HashMap::new(),
        }
    }

    /// Check if task can be executed (all dependencies met)
    pub fn is_ready(&self) -> bool {
        self.status == TaskStatus::Pending && self.dependencies.is_empty()
    }

    /// Check if task is in a terminal state
    pub fn is_terminal(&self) -> bool {
        matches!(
            self.status,
            TaskStatus::Success | TaskStatus::Failed | TaskStatus::Cancelled
        )
    }

    /// Check if task should be retried
    pub fn should_retry(&self) -> bool {
        if let Some(policy) = &self.retry_policy {
            matches!(self.status, TaskStatus::Failed | TaskStatus::Retrying)
                && self.attempts < policy.max_attempts
        } else {
            false
        }
    }

    /// Calculate next retry delay
    pub fn next_retry_delay(&self) -> Option<Duration> {
        if let Some(policy) = &self.retry_policy {
            if self.should_retry() {
                let delay = policy.retry_delay.as_secs_f64()
                    * policy.backoff_multiplier.powi(self.attempts as i32);
                let delay = Duration::from_secs_f64(delay);
                Some(delay.min(policy.max_retry_delay))
            } else {
                None
            }
        } else {
            None
        }
    }

    /// Mark task as running
    pub fn mark_running(&mut self) {
        self.status = TaskStatus::Running;
        self.attempts += 1;
    }

    /// Mark task as successful
    pub fn mark_success(&mut self) {
        self.status = TaskStatus::Success;
    }

    /// Mark task as failed
    pub fn mark_failed(&mut self) {
        // Check if we can retry before changing status
        let can_retry = if let Some(policy) = &self.retry_policy {
            self.attempts < policy.max_attempts
        } else {
            false
        };

        if can_retry {
            self.status = TaskStatus::Retrying;
        } else {
            self.status = TaskStatus::Failed;
        }
    }

    /// Mark task as cancelled
    pub fn mark_cancelled(&mut self) {
        self.status = TaskStatus::Cancelled;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_task_creation() {
        let task = Task::new("test-task", "echo 'hello'");
        assert_eq!(task.name, "test-task");
        assert_eq!(task.run, "echo 'hello'");
        assert_eq!(task.status, TaskStatus::Pending);
        assert!(!task.id.is_empty());
    }

    #[test]
    fn test_task_status_transitions() {
        let mut task = Task::new("test", "echo test");
        
        assert_eq!(task.status, TaskStatus::Pending);
        assert!(task.is_ready());
        assert!(!task.is_terminal());

        task.mark_running();
        assert_eq!(task.status, TaskStatus::Running);
        assert_eq!(task.attempts, 1);
        assert!(!task.is_terminal());

        task.mark_success();
        assert_eq!(task.status, TaskStatus::Success);
        assert!(task.is_terminal());
    }

    #[test]
    fn test_retry_policy() {
        let mut task = Task::new("test", "echo test");
        task.retry_policy = Some(RetryPolicy::default());

        task.mark_running();
        task.mark_failed();
        assert_eq!(task.status, TaskStatus::Retrying);
        assert!(task.should_retry());

        let delay = task.next_retry_delay();
        assert!(delay.is_some());
    }

    #[test]
    fn test_task_dependencies() {
        let mut task = Task::new("dependent", "echo test");
        assert!(task.is_ready());

        task.dependencies.push("task-1".to_string());
        assert!(!task.is_ready());
    }
}
