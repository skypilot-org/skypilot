//! Storage traits for state management

use crate::{Result, Task};
use async_trait::async_trait;

/// Task storage interface
#[async_trait]
pub trait TaskStore: Send + Sync {
    /// Insert a new task
    async fn insert_task(&self, task: &Task) -> Result<()>;
    
    /// Update task status
    async fn update_task_status(&self, task_id: &str, status: &str) -> Result<()>;
    
    /// Get task by ID
    async fn get_task(&self, task_id: &str) -> Result<Option<Task>>;
    
    /// Get ready tasks (no pending dependencies)
    async fn get_ready_tasks(&self, limit: usize) -> Result<Vec<Task>>;
    
    /// Increment retry count
    async fn increment_retries(&self, task_id: &str) -> Result<()>;
    
    /// Resume in-flight tasks (RUNNING -> PENDING/RETRYING)
    async fn resume_inflight(&self) -> Result<Vec<Task>>;
}

/// Graph storage interface
#[async_trait]
pub trait GraphStore: Send + Sync {
    /// Add edge to graph
    async fn add_edge(&self, src: &str, dst: &str) -> Result<()>;
    
    /// Load graph for task IDs
    async fn load_graph(&self, task_ids: &[String]) -> Result<Vec<(String, String)>>;
    
    /// Get dependencies for a task
    async fn get_dependencies(&self, task_id: &str) -> Result<Vec<String>>;
}

/// Key-value storage interface
#[async_trait]
pub trait KvStore: Send + Sync {
    /// Get value by key
    async fn get(&self, key: &str) -> Result<Option<Vec<u8>>>;
    
    /// Set key-value pair
    async fn set(&self, key: &str, value: &[u8]) -> Result<()>;
    
    /// Delete key
    async fn delete(&self, key: &str) -> Result<()>;
}
