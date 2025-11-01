//! Multi-Task Example
//!
//! Submit multiple tasks with different priorities.

use styx_core::{Scheduler, SchedulerConfig, Task, TaskPriority};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let scheduler = Scheduler::new(SchedulerConfig::default());

    // Task 1: High priority
    let task1 = Task::new("task-1", "python")
        .with_arg("train_model.py".to_string())
        .with_priority(TaskPriority::High);

    // Task 2: Normal priority
    let task2 = Task::new("task-2", "python")
        .with_arg("preprocess.py".to_string())
        .with_priority(TaskPriority::Normal);

    // Task 3: Low priority
    let task3 = Task::new("task-3", "python")
        .with_arg("backup.py".to_string())
        .with_priority(TaskPriority::Low);

    // Submit all tasks
    let id1 = scheduler.submit(task1).await?;
    let id2 = scheduler.submit(task2).await?;
    let id3 = scheduler.submit(task3).await?;

    println!("? Submitted 3 tasks:");
    println!("  High priority:   {}", id1);
    println!("  Normal priority: {}", id2);
    println!("  Low priority:    {}", id3);

    // Get stats
    let stats = scheduler.stats().await;
    println!("\n?? Scheduler stats:");
    println!("  Total: {}", stats.total);
    println!("  Pending: {}", stats.pending);

    Ok(())
}
