//! Integration tests for DAG and Scheduler functionality

use stix_core::dag::Dag;
use stix_core::scheduler::{Scheduler, ExecutionResult, SchedulerConfig};
use stix_core::task::TaskBuilder;
use std::sync::Arc;
use tokio::sync::Mutex;

#[tokio::test]
async fn test_dag_scheduler_integration() {
    let scheduler = Scheduler::new();

    // Create a complex DAG
    let mut dag = Dag::new();

    // Task A (no dependencies)
    let task_a = TaskBuilder::new()
        .name("task_a")
        .run("echo 'Task A'")
        .build()
        .unwrap();

    // Task B (no dependencies)
    let task_b = TaskBuilder::new()
        .name("task_b")
        .run("echo 'Task B'")
        .build()
        .unwrap();

    // Task C (depends on A)
    let task_c = TaskBuilder::new()
        .name("task_c")
        .run("echo 'Task C'")
        .dependency(task_a.id.clone())
        .build()
        .unwrap();

    // Task D (depends on A and B)
    let task_d = TaskBuilder::new()
        .name("task_d")
        .run("echo 'Task D'")
        .dependency(task_a.id.clone())
        .dependency(task_b.id.clone())
        .build()
        .unwrap();

