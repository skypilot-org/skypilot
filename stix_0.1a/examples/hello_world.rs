//! Hello World Example
//! 
//! Simplest Styx example - submit a basic task.

use styx_core::{Scheduler, SchedulerConfig, Task, TaskPriority};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Create scheduler
    let scheduler = Scheduler::new(SchedulerConfig::default());

    // Create a simple task
    let task = Task::new("hello-world", "echo")
        .with_arg("Hello from Styx!".to_string())
        .with_priority(TaskPriority::Normal);

    // Submit task
    let task_id = scheduler.submit(task).await?;

    println!("? Task submitted: {}", task_id);
    println!("Task will execute: echo 'Hello from Styx!'");

    Ok(())
}
