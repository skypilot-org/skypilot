//! Batch Jobs Example
//!
//! Submit many tasks in parallel (parameter sweep).

use styx_core::{Scheduler, SchedulerConfig, Task, TaskPriority};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let scheduler = Scheduler::new(SchedulerConfig::default());

    println!("?? Submitting batch jobs...\n");

    // Hyperparameter sweep
    let learning_rates = vec![0.001, 0.01, 0.1, 1.0];
    let batch_sizes = vec![32, 64, 128, 256];

    let mut task_ids = Vec::new();

    for lr in &learning_rates {
        for batch_size in &batch_sizes {
            let task_name = format!("train-lr{}-bs{}", lr, batch_size);
            
            let task = Task::new(&task_name, "python")
                .with_arg("train.py".to_string())
                .with_arg("--lr".to_string())
                .with_arg(lr.to_string())
                .with_arg("--batch-size".to_string())
                .with_arg(batch_size.to_string())
                .with_priority(TaskPriority::Normal);

            let task_id = scheduler.submit(task).await?;
            task_ids.push((task_name, task_id));
        }
    }

    println!("? Submitted {} tasks:", task_ids.len());
    for (name, id) in task_ids.iter().take(5) {
        println!("  {} -> {}", name, id);
    }
    println!("  ... and {} more", task_ids.len() - 5);

    // Get stats
    let stats = scheduler.stats().await;
    println!("\n?? Scheduler stats:");
    println!("  Total tasks: {}", stats.total);
    println!("  Pending: {}", stats.pending);

    Ok(())
}
