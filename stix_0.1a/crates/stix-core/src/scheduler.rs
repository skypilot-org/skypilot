//! Task scheduler
//!
//! Provides parallel task scheduling with dependency resolution and error handling.

use crate::dag::{Dag, DagExecutor};
use crate::error::{Error, Result};
use crate::task::{TaskId, TaskStatus};
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::{Mutex, Notify};
use tracing::{info, warn, error};

/// Task execution result
#[derive(Debug, Clone)]
pub enum ExecutionResult {
    Success(TaskId),
    Failed(TaskId, String),
    Cancelled(TaskId),
}

/// Scheduler configuration
#[derive(Debug, Clone)]
pub struct SchedulerConfig {
    pub max_parallel_tasks: usize,
    pub enable_retry: bool,
    pub max_retries: u32,
}

impl Default for SchedulerConfig {
    fn default() -> Self {
        Self {
            max_parallel_tasks: 4,
            enable_retry: true,
            max_retries: 3,
        }
    }
}

/// Task scheduler with dependency-aware execution
pub struct Scheduler {
    config: SchedulerConfig,
    dag_executor: DagExecutor,
    running_tasks: Arc<Mutex<HashMap<TaskId, tokio::task::JoinHandle<()>>>>,
    notify: Arc<Notify>,
}

impl Scheduler {
    /// Create a new scheduler with default config
    pub fn new() -> Self {
        Self::with_config(SchedulerConfig::default())
    }

    /// Create a new scheduler with custom config
    pub fn with_config(config: SchedulerConfig) -> Self {
        Self {
            dag_executor: DagExecutor::with_parallelism(config.max_parallel_tasks),
            config,
            running_tasks: Arc::new(Mutex::new(HashMap::new())),
            notify: Arc::new(Notify::new()),
        }
    }

    /// Schedule and execute a DAG
    pub async fn schedule_dag(&self, dag: Arc<Mutex<Dag>>) -> Result<Vec<ExecutionResult>> {
        info!("Starting DAG execution");

        // Validate DAG first
        {
            let dag_guard = dag.lock().await;
            dag_guard.validate()?;
        }

        let mut results = Vec::new();
        let mut completed_tasks = std::collections::HashSet::new();

        loop {
            // Get ready tasks
            let ready_tasks = {
                let dag_guard = dag.lock().await;
                dag_guard.get_ready_tasks()
                    .into_iter()
                    .filter(|id| !completed_tasks.contains(id))
                    .collect::<Vec<_>>()
            };

            if ready_tasks.is_empty() {
                // Check if all tasks are done
                let all_done = {
                    let dag_guard = dag.lock().await;
                    dag_guard.graph.task_indices.keys().all(|id| {
                        if let Some(task) = dag_guard.get_task(id) {
                            matches!(task.status, TaskStatus::Success | TaskStatus::Failed | TaskStatus::Cancelled)
                        } else {
                            false
                        }
                    })
                };

                if all_done {
                    break;
                }
            }

            // Execute ready tasks up to parallelism limit
            let tasks_to_start = ready_tasks.len().min(self.config.max_parallel_tasks);

            for task_id in ready_tasks.into_iter().take(tasks_to_start) {
                let dag_clone = Arc::clone(&dag);
                let task_id_clone = task_id.clone();
                let notify = Arc::clone(&self.notify);

                let handle = tokio::spawn(async move {
                    let result = Self::execute_task(dag_clone, task_id_clone).await;
                    notify.notify_waiters();
                    result
                });

                {
                    let mut running = self.running_tasks.lock().await;
                    running.insert(task_id.clone(), handle);
                }

                completed_tasks.insert(task_id);
            }

            // Wait for task completion or timeout
            tokio::select! {
                _ = self.notify.notified() => {
                    // A task completed, continue the loop
                }
                _ = tokio::time::sleep(tokio::time::Duration::from_secs(30)) => {
                    // Timeout, check for stuck tasks
                    warn!("Scheduler timeout, checking for stuck tasks");
                }
            }
        }

        // Collect final results
        {
            let dag_guard = dag.lock().await;
            for task_id in dag_guard.graph.task_indices.keys() {
                if let Some(task) = dag_guard.get_task(task_id) {
                    let result = match task.status {
                        TaskStatus::Success => ExecutionResult::Success(task_id.clone()),
                        TaskStatus::Failed => ExecutionResult::Failed(task_id.clone(), "Task failed".to_string()),
                        TaskStatus::Cancelled => ExecutionResult::Cancelled(task_id.clone()),
                        _ => continue, // Skip pending/retrying tasks
                    };
                    results.push(result);
                }
            }
        }

        info!("DAG execution completed with {} results", results.len());
        Ok(results)
    }

    /// Execute a single task
    async fn execute_task(dag: Arc<Mutex<Dag>>, task_id: TaskId) -> ExecutionResult {
        info!("Executing task: {}", task_id);

        // Mark task as running
        {
            let mut dag_guard = dag.lock().await;
            if let Some(task) = dag_guard.get_task_mut(&task_id) {
                task.mark_running();
            }
        }

        // Simulate task execution (replace with actual execution logic)
        let success = Self::simulate_task_execution(&task_id).await;

        // Update task status
        {
            let mut dag_guard = dag.lock().await;
            if let Some(task) = dag_guard.get_task_mut(&task_id) {
                if success {
                    task.mark_success();
                    info!("Task {} completed successfully", task_id);
                    ExecutionResult::Success(task_id)
                } else {
                    task.mark_failed();
                    error!("Task {} failed", task_id);
                    ExecutionResult::Failed(task_id, "Execution failed".to_string())
                }
            } else {
                ExecutionResult::Failed(task_id, "Task not found".to_string())
            }
        }
    }

    /// Simulate task execution (replace with actual command execution)
    async fn simulate_task_execution(task_id: &TaskId) -> bool {
        // Simulate variable execution time
        let duration = match task_id.as_str() {
            "task1" => tokio::time::Duration::from_millis(500),
            "task2" => tokio::time::Duration::from_millis(300),
            "task3" => tokio::time::Duration::from_millis(700),
            _ => tokio::time::Duration::from_millis(200),
        };

        tokio::time::sleep(duration).await;

        // Simulate occasional failures
        !task_id.contains("fail")
    }

    /// Cancel a running task
    pub async fn cancel_task(&self, task_id: &TaskId) -> Result<()> {
        let mut running = self.running_tasks.lock().await;
        if let Some(handle) = running.remove(task_id) {
            handle.abort();

            // Update task status
            // Note: In real implementation, this would need access to the DAG
            warn!("Cancelled task: {}", task_id);
        }

        Ok(())
    }

    /// Get scheduler statistics
    pub async fn stats(&self) -> SchedulerStats {
        let running_count = self.running_tasks.lock().await.len();

        SchedulerStats {
            running_tasks: running_count,
            max_parallel: self.config.max_parallel_tasks,
        }
    }
}

/// Scheduler statistics
#[derive(Debug, Clone)]
pub struct SchedulerStats {
    pub running_tasks: usize,
    pub max_parallel: usize,
}

impl Default for Scheduler {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dag::Dag;
    use crate::task::TaskBuilder;
    use std::sync::Arc;

    #[tokio::test]
    async fn test_scheduler_creation() {
        let scheduler = Scheduler::new();
        let stats = scheduler.stats().await;
        assert_eq!(stats.max_parallel, 4);
        assert_eq!(stats.running_tasks, 0);
    }

    #[tokio::test]
    async fn test_schedule_simple_dag() {
        let scheduler = Scheduler::new();

        // Create a simple DAG
        let mut dag = Dag::new();
        let task1 = TaskBuilder::new()
            .name("task1")
            .run("echo hello")
            .build()
            .unwrap();

        let task2 = TaskBuilder::new()
            .name("task2")
            .run("echo world")
            .dependency(task1.id.clone())
            .build()
            .unwrap();

        dag.add_task(task1).unwrap();
        dag.add_task(task2).unwrap();

        let dag = Arc::new(Mutex::new(dag));
        let results = scheduler.schedule_dag(dag).await.unwrap();

        assert_eq!(results.len(), 2);
        // Check that both tasks succeeded
        assert!(results.iter().all(|r| matches!(r, ExecutionResult::Success(_))));
    }

    #[tokio::test]
    async fn test_schedule_parallel_tasks() {
        let scheduler = Scheduler::new();

        let mut dag = Dag::new();

        // Create independent tasks
        let task1 = TaskBuilder::new()
            .name("task1")
            .run("echo task1")
            .build()
            .unwrap();

        let task2 = TaskBuilder::new()
            .name("task2")
            .run("echo task2")
            .build()
            .unwrap();

        let task3 = TaskBuilder::new()
            .name("task3")
            .run("echo task3")
            .build()
            .unwrap();

        dag.add_task(task1).unwrap();
        dag.add_task(task2).unwrap();
        dag.add_task(task3).unwrap();

        let dag = Arc::new(Mutex::new(dag));
        let results = scheduler.schedule_dag(dag).await.unwrap();

        assert_eq!(results.len(), 3);
        assert!(results.iter().all(|r| matches!(r, ExecutionResult::Success(_))));
    }

    #[tokio::test]
    async fn test_scheduler_config() {
        let config = SchedulerConfig {
            max_parallel_tasks: 8,
            enable_retry: false,
            max_retries: 1,
        };

        let scheduler = Scheduler::with_config(config);
        let stats = scheduler.stats().await;
        assert_eq!(stats.max_parallel, 8);
    }
}
