//! DAG - Directed Acyclic Graph for task dependencies
//!
//! Python: sky/dag.py (128 LoC)
//! Rust: styx-sky/src/dag.rs

use petgraph::graph::{DiGraph, NodeIndex};
use petgraph::visit::Topo;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

use crate::exceptions::{dag_validation_error, Result};
use crate::task::Task;

/// DAG - Directed Acyclic Graph for task orchestration
///
/// Python equivalent:
/// ```python
/// import sky
/// dag = sky.Dag()
/// dag.add(task1)
/// dag.add(task2, deps=[task1])
/// ```
///
/// Rust equivalent:
/// ```rust
/// use styx_sky::Dag;
/// let mut dag = Dag::new();
/// let task1_id = dag.add(task1, vec![]);
/// let task2_id = dag.add(task2, vec![task1_id]);
/// ```
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Dag {
    /// Internal graph structure
    graph: DiGraph<Task, ()>,

    /// Task name to node index mapping
    name_to_node: HashMap<String, NodeIndex>,

    /// DAG name
    name: Option<String>,
}

impl Dag {
    /// Creates a new empty DAG
    ///
    /// Python: `dag = sky.Dag()`
    pub fn new() -> Self {
        Self {
            graph: DiGraph::new(),
            name_to_node: HashMap::new(),
            name: None,
        }
    }

    /// Creates a named DAG
    ///
    /// Python: `dag = sky.Dag(name='my-dag')`
    pub fn with_name(name: impl Into<String>) -> Self {
        Self {
            graph: DiGraph::new(),
            name_to_node: HashMap::new(),
            name: Some(name.into()),
        }
    }

    /// Adds a task to the DAG
    ///
    /// Python:
    /// ```python
    /// dag.add(task)
    /// dag.add(task2, deps=[task1])
    /// ```
    ///
    /// Rust:
    /// ```
    /// let task1_id = dag.add(task1, vec![]);
    /// let task2_id = dag.add(task2, vec![task1_id]);
    /// ```
    pub fn add(&mut self, task: Task, deps: Vec<NodeIndex>) -> Result<NodeIndex> {
        // Add node
        let node_idx = self.graph.add_node(task.clone());

        // Add to name mapping
        if let Some(ref name) = task.name() {
            self.name_to_node.insert(name.clone(), node_idx);
        }

        // Add dependency edges
        for dep in deps {
            self.graph.add_edge(dep, node_idx, ());
        }

        // Validate no cycles
        if self.has_cycle() {
            return Err(dag_validation_error("DAG contains a cycle"));
        }

        Ok(node_idx)
    }

    /// Adds a task by name (looks up dependency by name)
    ///
    /// Python:
    /// ```python
    /// dag.add(task2, deps=['task1'])
    /// ```
    pub fn add_with_named_deps(&mut self, task: Task, dep_names: Vec<&str>) -> Result<NodeIndex> {
        let dep_nodes: Result<Vec<NodeIndex>> = dep_names
            .iter()
            .map(|name| {
                self.name_to_node
                    .get(*name)
                    .copied()
                    .ok_or_else(|| dag_validation_error(format!("Task '{}' not found", name)))
            })
            .collect();

        self.add(task, dep_nodes?)
    }

    /// Returns tasks in topological order
    ///
    /// Python: `for task in dag.tasks: ...`
    pub fn topological_order(&self) -> Vec<Task> {
        let mut topo = Topo::new(&self.graph);
        let mut tasks = Vec::new();

        while let Some(node_idx) = topo.next(&self.graph) {
            if let Some(task) = self.graph.node_weight(node_idx) {
                tasks.push(task.clone());
            }
        }

        tasks
    }

    /// Checks if DAG has a cycle
    fn has_cycle(&self) -> bool {
        petgraph::algo::is_cyclic_directed(&self.graph)
    }

    /// Returns the number of tasks
    pub fn len(&self) -> usize {
        self.graph.node_count()
    }

    /// Checks if DAG is empty
    pub fn is_empty(&self) -> bool {
        self.graph.node_count() == 0
    }

    /// Returns DAG name
    pub fn name(&self) -> Option<&str> {
        self.name.as_deref()
    }

    /// Gets a task by name
    pub fn get_task(&self, name: &str) -> Option<&Task> {
        self.name_to_node
            .get(name)
            .and_then(|&idx| self.graph.node_weight(idx))
    }

    /// Returns all tasks
    pub fn tasks(&self) -> Vec<&Task> {
        self.graph
            .node_indices()
            .filter_map(|idx| self.graph.node_weight(idx))
            .collect()
    }

    /// Validates the DAG
    pub fn validate(&self) -> Result<()> {
        // Check for cycles
        if self.has_cycle() {
            return Err(dag_validation_error("DAG contains a cycle"));
        }

        // Check that all tasks are valid
        for task in self.tasks() {
            task.validate()?;
        }

        Ok(())
    }
}

impl Default for Dag {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_dag_creation() {
        let dag = Dag::new();
        assert!(dag.is_empty());
        assert_eq!(dag.len(), 0);
    }

    #[test]
    fn test_dag_add_task() {
        let mut dag = Dag::new();
        let task = Task::new("test-task", "echo hello");

        let node_idx = dag.add(task, vec![]).unwrap();
        assert_eq!(dag.len(), 1);
    }

    #[test]
    fn test_dag_dependencies() {
        let mut dag = Dag::new();

        let task1 = Task::new("task1", "echo 1");
        let task2 = Task::new("task2", "echo 2");

        let node1 = dag.add(task1, vec![]).unwrap();
        let node2 = dag.add(task2, vec![node1]).unwrap();

        assert_eq!(dag.len(), 2);

        let tasks = dag.topological_order();
        assert_eq!(tasks.len(), 2);
        assert_eq!(tasks[0].name(), Some(&"task1".to_string()));
        assert_eq!(tasks[1].name(), Some(&"task2".to_string()));
    }

    #[test]
    fn test_dag_cycle_detection() {
        let mut dag = Dag::new();

        let node_a = dag.add(Task::new("a", "echo a"), vec![]).unwrap();
        let node_b = dag.add(Task::new("b", "echo b"), vec![node_a]).unwrap();

        // Force a cycle by linking b -> a manually
        dag.graph.add_edge(node_b, node_a, ());
        assert!(dag.has_cycle());

        let result = dag.add(Task::new("c", "echo c"), vec![node_a]);
        assert!(result.is_err());
    }

    #[test]
    fn test_dag_with_name() {
        let dag = Dag::with_name("my-dag");
        assert_eq!(dag.name(), Some("my-dag"));
    }
}
