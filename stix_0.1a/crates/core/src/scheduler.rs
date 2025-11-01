//! Task scheduler and execution engine

use crate::error::{Error, Result};
use crate::task::{Task, TaskId, TaskStatus};
use petgraph::graph::{DiGraph, NodeIndex};
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::{mpsc, RwLock};
use tracing::{debug, info, warn};

/// Scheduler configuration
#[derive(Debug, Clone)]
pub struct SchedulerConfig {
    /// Maximum concurrent tasks
    pub max_concurrent: usize,
    /// Queue capacity
    pub queue_capacity: usize,
}

impl Default for SchedulerConfig {
    fn default() -> Self {
        Self {
            max_concurrent: 10,
            queue_capacity: 1000,
        }
    }
}

/// Scheduler event
#[derive(Debug, Clone)]
pub enum SchedulerEvent {
    /// Task was submitted
    TaskSubmitted(TaskId),
    /// Task started execution
    TaskStarted(TaskId),
    /// Task completed successfully
    TaskCompleted(TaskId),
    /// Task failed
    TaskFailed(TaskId, String),
    /// Scheduler stopped
    Stopped,
}

/// Task scheduler
pub struct Scheduler {
    config: SchedulerConfig,
    tasks: Arc<RwLock<HashMap<TaskId, Task>>>,
    dag: Arc<RwLock<DiGraph<TaskId, ()>>>,
    task_to_node: Arc<RwLock<HashMap<TaskId, NodeIndex>>>,
    event_tx: mpsc::UnboundedSender<SchedulerEvent>,
    event_rx: Option<mpsc::UnboundedReceiver<SchedulerEvent>>,
}

impl Scheduler {
    /// Create a new scheduler
    pub fn new(config: SchedulerConfig) -> Self {
        let (event_tx, event_rx) = mpsc::unbounded_channel();

        Self {
            config,
            tasks: Arc::new(RwLock::new(HashMap::new())),
            dag: Arc::new(RwLock::new(DiGraph::new())),
            task_to_node: Arc::new(RwLock::new(HashMap::new())),
            event_tx,
            event_rx: Some(event_rx),
        }
    }

    /// Take the event receiver (can only be called once)
    pub fn take_events(&mut self) -> Option<mpsc::UnboundedReceiver<SchedulerEvent>> {
        self.event_rx.take()
    }

    /// Submit a task for execution
    pub async fn submit(&self, task: Task) -> Result<TaskId> {
        let task_id = task.id;

        info!("Submitting task: {} ({})", task.name, task_id);

        // Add to tasks map
        {
            let mut tasks = self.tasks.write().await;
            tasks.insert(task_id, task.clone());
        }

        // Add to DAG
        {
            let mut dag = self.dag.write().await;
            let mut task_to_node = self.task_to_node.write().await;

            let node = dag.add_node(task_id);
            task_to_node.insert(task_id, node);

            // Add dependency edges
            for dep_id in &task.dependencies {
                if let Some(&dep_node) = task_to_node.get(dep_id) {
                    dag.add_edge(dep_node, node, ());
                } else {
                    warn!("Dependency {} not found for task {}", dep_id, task_id);
                }
            }
        }

        // Send event
        let _ = self.event_tx.send(SchedulerEvent::TaskSubmitted(task_id));

        Ok(task_id)
    }

    /// Get task by ID
    pub async fn get_task(&self, task_id: TaskId) -> Option<Task> {
        let tasks = self.tasks.read().await;
        tasks.get(&task_id).cloned()
    }

    /// Get all tasks
    pub async fn get_all_tasks(&self) -> Vec<Task> {
        let tasks = self.tasks.read().await;
        tasks.values().cloned().collect()
    }

    /// Get ready tasks (all dependencies completed)
    pub async fn get_ready_tasks(&self) -> Vec<TaskId> {
        let tasks = self.tasks.read().await;
        let dag = self.dag.read().await;
        let task_to_node = self.task_to_node.read().await;

        let mut ready = Vec::new();

        for (&task_id, task) in tasks.iter() {
            if task.status != TaskStatus::Pending {
                continue;
            }

            // Check if all dependencies are completed
            let all_deps_completed = task.dependencies.iter().all(|dep_id| {
                if let Some(dep_task) = tasks.get(dep_id) {
                    dep_task.status == TaskStatus::Completed
                } else {
                    false
                }
            });

            if all_deps_completed {
                ready.push(task_id);
            }
        }

        ready
    }

    /// Update task status
    pub async fn update_status(&self, task_id: TaskId, status: TaskStatus) -> Result<()> {
        let mut tasks = self.tasks.write().await;

        if let Some(task) = tasks.get_mut(&task_id) {
            debug!("Updating task {} status to {:?}", task_id, status);

            match &status {
                TaskStatus::Running => task.mark_running(),
                TaskStatus::Completed => task.mark_completed(),
                TaskStatus::Failed(err) => task.mark_failed(err),
                _ => task.status = status.clone(),
            }

            // Send event
            match status {
                TaskStatus::Running => {
                    let _ = self.event_tx.send(SchedulerEvent::TaskStarted(task_id));
                }
                TaskStatus::Completed => {
                    let _ = self.event_tx.send(SchedulerEvent::TaskCompleted(task_id));
                }
                TaskStatus::Failed(err) => {
                    let _ = self.event_tx.send(SchedulerEvent::TaskFailed(task_id, err));
                }
                _ => {}
            }

            Ok(())
        } else {
            Err(Error::NotFound(format!("Task {} not found", task_id)))
        }
    }

    /// Get scheduler statistics
    pub async fn stats(&self) -> SchedulerStats {
        let tasks = self.tasks.read().await;

        let mut stats = SchedulerStats::default();
        stats.total = tasks.len();

        for task in tasks.values() {
            match task.status {
                TaskStatus::Pending => stats.pending += 1,
                TaskStatus::Running => stats.running += 1,
                TaskStatus::Completed => stats.completed += 1,
                TaskStatus::Failed(_) => stats.failed += 1,
                TaskStatus::Cancelled => stats.cancelled += 1,
            }
        }

        stats
    }
}

/// Scheduler statistics
#[derive(Debug, Default, Clone)]
pub struct SchedulerStats {
    pub total: usize,
    pub pending: usize,
    pub running: usize,
    pub completed: usize,
    pub failed: usize,
    pub cancelled: usize,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_scheduler_submit() {
        let scheduler = Scheduler::new(SchedulerConfig::default());
        let task = Task::new("test", "echo");

        let task_id = scheduler.submit(task).await.unwrap();
        let retrieved = scheduler.get_task(task_id).await.unwrap();

        assert_eq!(retrieved.name, "test");
        assert_eq!(retrieved.status, TaskStatus::Pending);
    }

    #[tokio::test]
    async fn test_scheduler_ready_tasks() {
        let scheduler = Scheduler::new(SchedulerConfig::default());

        let task1 = Task::new("task1", "cmd1");
        let id1 = scheduler.submit(task1).await.unwrap();

        let task2 = Task::new("task2", "cmd2").with_dependency(id1);
        let id2 = scheduler.submit(task2).await.unwrap();

        // Initially, only task1 is ready
        let ready = scheduler.get_ready_tasks().await;
        assert_eq!(ready.len(), 1);
        assert_eq!(ready[0], id1);

        // Complete task1
        scheduler
            .update_status(id1, TaskStatus::Completed)
            .await
            .unwrap();

        // Now task2 should be ready
        let ready = scheduler.get_ready_tasks().await;
        assert_eq!(ready.len(), 1);
        assert_eq!(ready[0], id2);
    }
}
