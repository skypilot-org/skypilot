//! Directed Acyclic Graph execution
//!
//! Provides dependency-aware task execution with cycle detection and parallel processing.

use crate::error::{Error, Result};
use crate::task::{Task, TaskId, TaskStatus};
use daggy::{Dag as DaggyDag, NodeIndex, Walker};
use petgraph::algo::toposort;
use std::collections::{HashMap, HashSet};
use tokio::sync::Mutex;

/// Node in the DAG representing a task
#[derive(Debug, Clone)]
pub struct DagNode {
    pub task_id: TaskId,
    pub task: Task,
}

/// Edge representing dependency relationship
#[derive(Debug, Clone)]
pub struct DagEdge {
    /// Source task depends on target task
    pub from: TaskId,
    pub to: TaskId,
}

/// Task graph with dependency relationships
#[derive(Debug)]
pub struct TaskGraph {
    /// Underlying graph structure
    graph: DaggyDag<DagNode, DagEdge>,
    /// Quick lookup from task ID to node index
    task_indices: HashMap<TaskId, NodeIndex>,
}

impl TaskGraph {
    /// Create a new empty task graph
    pub fn new() -> Self {
        Self {
            graph: DaggyDag::new(),
            task_indices: HashMap::new(),
        }
    }

    /// Add a task to the graph
    pub fn add_task(&mut self, task: Task) -> Result<NodeIndex> {
        let node = DagNode {
            task_id: task.id.clone(),
            task,
        };
        let node_idx = self.graph.add_node(node);
        self.task_indices.insert(node_idx.index().to_string(), node_idx);
        Ok(node_idx)
    }

    /// Add a dependency edge (from depends on to)
    pub fn add_dependency(&mut self, from_id: &TaskId, to_id: &TaskId) -> Result<()> {
        let from_idx = self.task_indices.get(from_id)
            .ok_or_else(|| Error::dag_validation(format!("Task {} not found", from_id)))?;
        let to_idx = self.task_indices.get(to_id)
            .ok_or_else(|| Error::dag_validation(format!("Task {} not found", to_id)))?;

        let edge = DagEdge {
            from: from_id.clone(),
            to: to_id.clone(),
        };

        self.graph.add_edge(*from_idx, *to_idx, edge)
            .map_err(|_| Error::dag_validation("Failed to add dependency edge"))?;
        Ok(())
    }

    /// Get topological order of tasks
    pub fn topological_order(&self) -> Result<Vec<TaskId>> {
        let node_indices: Vec<_> = self.graph.node_indices().collect();
        let topo_result = toposort(&self.graph.graph(), Some(&node_indices));

        match topo_result {
            Ok(indices) => {
                let mut task_ids = Vec::new();
                for idx in indices {
                    if let Some(node) = self.graph.node_weight(idx) {
                        task_ids.push(node.task_id.clone());
                    }
                }
                Ok(task_ids)
            }
            Err(cycle) => {
                let cycle_node = self.graph.node_weight(cycle.node_id())
                    .map(|n| n.task_id.clone())
                    .unwrap_or_else(|| "unknown".to_string());
                Err(Error::dag_validation(format!("Cycle detected involving task {}", cycle_node)))
            }
        }
    }

    /// Get tasks that are ready to execute (all dependencies satisfied)
    pub fn get_ready_tasks(&self) -> Vec<TaskId> {
        let mut ready = Vec::new();

        for node_idx in self.graph.node_indices() {
            if let Some(node) = self.graph.node_weight(node_idx) {
                // Check if all dependencies are satisfied
                let mut deps_satisfied = true;
                let mut parents = self.graph.parents(node_idx);

                while let Some(parent_edge) = parents.walk_next(&self.graph) {
                    if let Some(parent_node) = self.graph.node_weight(parent_edge.node) {
                        if !matches!(parent_node.task.status, TaskStatus::Success) {
                            deps_satisfied = false;
                            break;
                        }
                    }
                }

                if deps_satisfied && node.task.status == TaskStatus::Pending {
                    ready.push(node.task_id.clone());
                }
            }
        }

        ready
    }

    /// Get task by ID
    pub fn get_task(&self, task_id: &TaskId) -> Option<&Task> {
        self.task_indices.get(task_id)
            .and_then(|idx| self.graph.node_weight(*idx))
            .map(|node| &node.task)
    }

    /// Get mutable task by ID
    pub fn get_task_mut(&mut self, task_id: &TaskId) -> Option<&mut Task> {
        if let Some(idx) = self.task_indices.get(task_id) {
            self.graph.node_weight_mut(*idx).map(|node| &mut node.task)
        } else {
            None
        }
    }

    /// Validate the graph (no cycles, all dependencies exist)
    pub fn validate(&self) -> Result<()> {
        // Check for cycles using topological sort
        self.topological_order()?;

        // Check that all dependency references exist
        for node_idx in self.graph.node_indices() {
            if let Some(node) = self.graph.node_weight(node_idx) {
                for dep_id in &node.task.dependencies {
                    if !self.task_indices.contains_key(dep_id) {
                        return Err(Error::dag_validation(format!(
                            "Task {} depends on non-existent task {}",
                            node.task_id, dep_id
                        )));
                    }
                }
            }
        }

        Ok(())
    }
}

impl Default for TaskGraph {
    fn default() -> Self {
        Self::new()
    }
}

/// Directed Acyclic Graph of tasks
#[derive(Debug)]
pub struct Dag {
    graph: TaskGraph,
}

impl Dag {
    /// Create a new empty DAG
    pub fn new() -> Self {
        Self {
            graph: TaskGraph::new(),
        }
    }

    /// Add a task to the DAG
    pub fn add_task(&mut self, task: Task) -> Result<()> {
        self.graph.add_task(task)?;

        // Add dependency edges based on task dependencies
        let task_ids: Vec<_> = self.graph.task_indices.keys().cloned().collect();
        for task_id in task_ids {
            if let Some(task) = self.graph.get_task(&task_id) {
                for dep_id in &task.dependencies {
                    self.graph.add_dependency(&task_id, dep_id)?;
                }
            }
        }

        Ok(())
    }

    /// Get a task by ID
    pub fn get_task(&self, id: &TaskId) -> Option<&Task> {
        self.graph.get_task(id)
    }

    /// Get mutable task by ID
    pub fn get_task_mut(&mut self, id: &TaskId) -> Option<&mut Task> {
        self.graph.get_task_mut(id)
    }

    /// Validate the DAG (no cycles, all dependencies exist)
    pub fn validate(&self) -> Result<()> {
        self.graph.validate()
    }

    /// Get topological order
    pub fn topological_order(&self) -> Result<Vec<TaskId>> {
        self.graph.topological_order()
    }

    /// Get ready tasks
    pub fn get_ready_tasks(&self) -> Vec<TaskId> {
        self.graph.get_ready_tasks()
    }
}

impl Default for Dag {
    fn default() -> Self {
        Self::new()
    }
}

/// DAG executor for parallel task execution
pub struct DagExecutor {
    max_parallel: usize,
}

impl DagExecutor {
    /// Create a new DAG executor
    pub fn new() -> Self {
        Self { max_parallel: 4 }
    }

    /// Create executor with custom parallelism
    pub fn with_parallelism(max_parallel: usize) -> Self {
        Self { max_parallel }
    }

    /// Execute a DAG with parallel processing
    pub async fn execute(&self, dag: &Mutex<Dag>) -> Result<()> {
        let mut completed = HashSet::new();
        let mut in_progress = HashSet::new();

        loop {
            // Get ready tasks
            let ready_tasks = {
                let dag_guard = dag.lock().await;
                dag_guard.get_ready_tasks()
            };

            // Filter out tasks already in progress or completed
            let new_tasks: Vec<_> = ready_tasks
                .into_iter()
                .filter(|id| !completed.contains(id) && !in_progress.contains(id))
                .collect();

            if new_tasks.is_empty() && in_progress.is_empty() {
                break; // All done
            }

            // Limit parallelism
            let tasks_to_start = new_tasks.len().min(self.max_parallel - in_progress.len());

            // Start new tasks
            for task_id in new_tasks.into_iter().take(tasks_to_start) {
                in_progress.insert(task_id.clone());
                let dag_clone = dag.clone();

                tokio::spawn(async move {
                    Self::execute_task(dag_clone, task_id).await;
                });
            }

            // Wait a bit before checking again
            tokio::time::sleep(tokio::time::Duration::from_millis(100)).await;
        }

        Ok(())
    }

    /// Execute a single task (mock implementation)
    async fn execute_task(dag: Mutex<Dag>, task_id: TaskId) {
        // Mark task as running
        {
            let mut dag_guard = dag.lock().await;
            if let Some(task) = dag_guard.get_task_mut(&task_id) {
                task.mark_running();
            }
        }

        // Simulate task execution
        tokio::time::sleep(tokio::time::Duration::from_millis(500)).await;

        // Mark task as completed (success for now)
        {
            let mut dag_guard = dag.lock().await;
            if let Some(task) = dag_guard.get_task_mut(&task_id) {
                task.mark_success();
            }
        }
    }
}

impl Default for DagExecutor {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::task::TaskBuilder;

    #[test]
    fn test_dag_creation() {
        let dag = Dag::new();
        assert!(dag.graph.task_indices.is_empty());
    }

    #[test]
    fn test_add_task() {
        let mut dag = Dag::new();
        let task = TaskBuilder::new()
            .name("task1")
            .run("echo hello")
            .build()
            .unwrap();

        assert!(dag.add_task(task).is_ok());
        assert_eq!(dag.graph.task_indices.len(), 1);
    }

    #[test]
    fn test_topological_order() {
        let mut dag = Dag::new();

        // Add tasks
        let task1 = TaskBuilder::new()
            .name("task1")
            .run("echo task1")
            .build()
            .unwrap();

        let task2 = TaskBuilder::new()
            .name("task2")
            .run("echo task2")
            .dependency(task1.id.clone())
            .build()
            .unwrap();

        dag.add_task(task1).unwrap();
        dag.add_task(task2).unwrap();

        let order = dag.topological_order().unwrap();
        assert_eq!(order.len(), 2);
        // task1 should come before task2
        assert!(order.iter().position(|id| *id == "task1").unwrap() <
                order.iter().position(|id| *id == "task2").unwrap());
    }

    #[test]
    fn test_cycle_detection() {
        let mut dag = Dag::new();

        let task1 = TaskBuilder::new()
            .name("task1")
            .run("echo task1")
            .build()
            .unwrap();

        let task2 = TaskBuilder::new()
            .name("task2")
            .run("echo task2")
            .dependency(task1.id.clone())
            .build()
            .unwrap();

        // Manually create cycle by adding reverse dependency
        dag.add_task(task1.clone()).unwrap();
        dag.add_task(task2.clone()).unwrap();

        // This should fail validation due to cycle
        let mut graph = TaskGraph::new();
        graph.add_task(task1).unwrap();
        graph.add_task(task2).unwrap();
        graph.add_dependency(&"task1".to_string(), &"task2".to_string()).unwrap();
        graph.add_dependency(&"task2".to_string(), &"task1".to_string()).unwrap();

        assert!(graph.validate().is_err());
    }

    #[test]
    fn test_ready_tasks() {
        let mut dag = Dag::new();

        let task1 = TaskBuilder::new()
            .name("task1")
            .run("echo task1")
            .build()
            .unwrap();

        let task2 = TaskBuilder::new()
            .name("task2")
            .run("echo task2")
            .dependency(task1.id.clone())
            .build()
            .unwrap();

        dag.add_task(task1).unwrap();
        dag.add_task(task2).unwrap();

        let ready = dag.get_ready_tasks();
        assert_eq!(ready.len(), 1);
        assert_eq!(ready[0], "task1");

        // Mark task1 as completed
        if let Some(task) = dag.get_task_mut(&"task1".to_string()) {
            task.mark_success();
        }

        let ready = dag.get_ready_tasks();
        assert_eq!(ready.len(), 1);
        assert_eq!(ready[0], "task2");
    }
}
