//! Task management and execution

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use uuid::Uuid;

/// Unique task identifier
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct TaskId(Uuid);

impl TaskId {
    /// Create a new random task ID
    pub fn new() -> Self {
        Self(Uuid::new_v4())
    }

    /// Create from UUID
    pub fn from_uuid(uuid: Uuid) -> Self {
        Self(uuid)
    }
}

impl Default for TaskId {
    fn default() -> Self {
        Self::new()
    }
}

impl std::fmt::Display for TaskId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}

/// Task execution status
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum TaskStatus {
    /// Task is pending execution
    Pending,
    /// Task is currently running
    Running,
    /// Task completed successfully
    Completed,
    /// Task failed with error
    Failed(String),
    /// Task was cancelled
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

/// A schedulable task
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Task {
    /// Unique task identifier
    pub id: TaskId,

    /// Task name
    pub name: String,

    /// Task command to execute
    pub command: String,

    /// Task arguments
    pub args: Vec<String>,

    /// Environment variables
    pub env: HashMap<String, String>,

    /// Task priority
    pub priority: TaskPriority,

    /// Current status
    pub status: TaskStatus,

    /// Resource requirements
    pub resources: crate::resource::ResourceRequirements,

    /// Dependencies (task IDs that must complete first)
    pub dependencies: Vec<TaskId>,

    /// Creation timestamp
    pub created_at: DateTime<Utc>,

    /// Start timestamp (if started)
    pub started_at: Option<DateTime<Utc>>,

    /// Completion timestamp (if completed)
    pub completed_at: Option<DateTime<Utc>>,

    /// Task metadata
    pub metadata: HashMap<String, String>,
}

impl Task {
    /// Create a new task
    pub fn new(name: impl Into<String>, command: impl Into<String>) -> Self {
        Self {
            id: TaskId::new(),
            name: name.into(),
            command: command.into(),
            args: Vec::new(),
            env: HashMap::new(),
            priority: TaskPriority::Normal,
            status: TaskStatus::Pending,
            resources: crate::resource::ResourceRequirements::default(),
            dependencies: Vec::new(),
            created_at: Utc::now(),
            started_at: None,
            completed_at: None,
            metadata: HashMap::new(),
        }
    }

    /// Set task priority
    pub fn with_priority(mut self, priority: TaskPriority) -> Self {
        self.priority = priority;
        self
    }

    /// Add task argument
    pub fn with_arg(mut self, arg: impl Into<String>) -> Self {
        self.args.push(arg.into());
        self
    }

    /// Add environment variable
    pub fn with_env(mut self, key: impl Into<String>, value: impl Into<String>) -> Self {
        self.env.insert(key.into(), value.into());
        self
    }

    /// Add dependency
    pub fn with_dependency(mut self, task_id: TaskId) -> Self {
        self.dependencies.push(task_id);
        self
    }

    /// Mark task as running
    pub fn mark_running(&mut self) {
        self.status = TaskStatus::Running;
        self.started_at = Some(Utc::now());
    }

    /// Mark task as completed
    pub fn mark_completed(&mut self) {
        self.status = TaskStatus::Completed;
        self.completed_at = Some(Utc::now());
    }

    /// Mark task as failed
    pub fn mark_failed(&mut self, error: impl Into<String>) {
        self.status = TaskStatus::Failed(error.into());
        self.completed_at = Some(Utc::now());
    }

    /// Check if task is terminal (completed, failed, or cancelled)
    pub fn is_terminal(&self) -> bool {
        matches!(
            self.status,
            TaskStatus::Completed | TaskStatus::Failed(_) | TaskStatus::Cancelled
        )
    }

    /// Get execution duration if completed
    pub fn duration(&self) -> Option<chrono::Duration> {
        match (self.started_at, self.completed_at) {
            (Some(start), Some(end)) => Some(end - start),
            _ => None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_task_creation() {
        let task = Task::new("test-task", "echo")
            .with_arg("hello")
            .with_priority(TaskPriority::High);

        assert_eq!(task.name, "test-task");
        assert_eq!(task.command, "echo");
        assert_eq!(task.args, vec!["hello"]);
        assert_eq!(task.priority, TaskPriority::High);
        assert_eq!(task.status, TaskStatus::Pending);
    }

    #[test]
    fn test_task_status_transitions() {
        let mut task = Task::new("test", "cmd");

        assert_eq!(task.status, TaskStatus::Pending);
        assert!(!task.is_terminal());

        task.mark_running();
        assert_eq!(task.status, TaskStatus::Running);
        assert!(task.started_at.is_some());

        task.mark_completed();
        assert_eq!(task.status, TaskStatus::Completed);
        assert!(task.completed_at.is_some());
        assert!(task.is_terminal());
    }

    #[test]
    fn test_task_duration() {
        let mut task = Task::new("test", "cmd");
        task.mark_running();
        std::thread::sleep(std::time::Duration::from_millis(10));
        task.mark_completed();

        let duration = task.duration();
        assert!(duration.is_some());
        assert!(duration.unwrap().num_milliseconds() >= 10);
    }
}
