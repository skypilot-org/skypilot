//! Directed Acyclic Graph execution
//!
//! TODO: Implement full DAG functionality in Week 2

use crate::error::Result;
use crate::task::{Task, TaskId};
use std::collections::HashMap;

/// Directed Acyclic Graph of tasks
#[derive(Debug)]
pub struct Dag {
    tasks: HashMap<TaskId, Task>,
}

impl Dag {
    /// Create a new empty DAG
    pub fn new() -> Self {
        Self {
            tasks: HashMap::new(),
        }
    }

    /// Add a task to the DAG
    pub fn add_task(&mut self, task: Task) -> Result<()> {
        // TODO: Validate no cycles
        self.tasks.insert(task.id.clone(), task);
        Ok(())
    }

    /// Get a task by ID
    pub fn get_task(&self, id: &TaskId) -> Option<&Task> {
        self.tasks.get(id)
    }

    /// Validate the DAG (no cycles, all dependencies exist)
    pub fn validate(&self) -> Result<()> {
        // TODO: Implement cycle detection
        // TODO: Validate all dependencies exist
        Ok(())
    }
}

impl Default for Dag {
    fn default() -> Self {
        Self::new()
    }
}

/// DAG executor
pub struct DagExecutor;

impl DagExecutor {
    /// Execute a DAG
    pub async fn execute(_dag: &Dag) -> Result<()> {
        // TODO: Implement parallel execution
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_dag_creation() {
        let dag = Dag::new();
        assert!(dag.tasks.is_empty());
    }
}
