//! Task DAG Example
//!
//! Create tasks with dependencies (DAG = Directed Acyclic Graph).

use styx_core::{Scheduler, SchedulerConfig, Task, TaskPriority};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let scheduler = Scheduler::new(SchedulerConfig::default());

    // Task 1: Data download (no dependencies)
    let task1 = Task::new("download-data", "wget")
        .with_arg("https://example.com/dataset.tar.gz".to_string());

    let id1 = scheduler.submit(task1).await?;

    // Task 2: Data preprocessing (depends on task1)
    let task2 = Task::new("preprocess", "python")
        .with_arg("preprocess.py".to_string())
        .with_dependency(id1);

    let id2 = scheduler.submit(task2).await?;

    // Task 3: Model training (depends on task2)
    let task3 = Task::new("train", "python")
        .with_arg("train.py".to_string())
        .with_dependency(id2)
        .with_priority(TaskPriority::High);

    let id3 = scheduler.submit(task3).await?;

    // Task 4: Evaluation (depends on task3)
    let task4 = Task::new("evaluate", "python")
        .with_arg("evaluate.py".to_string())
        .with_dependency(id3);

    let id4 = scheduler.submit(task4).await?;

    println!("? Submitted DAG with 4 tasks:");
    println!("  1. download-data  -> {}", id1);
    println!("  2. preprocess     -> {} (depends on 1)", id2);
    println!("  3. train          -> {} (depends on 2)", id3);
    println!("  4. evaluate       -> {} (depends on 3)", id4);
    println!("\nExecution order will be: 1 ? 2 ? 3 ? 4");

    Ok(())
}
