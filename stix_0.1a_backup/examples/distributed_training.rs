//! Distributed Training Example
//!
//! Multi-node training with multiple workers.

use styx_core::{Scheduler, SchedulerConfig, Task, TaskPriority, ResourceRequirements};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let scheduler = Scheduler::new(SchedulerConfig::default());

    println!("?? Distributed Training Example\n");

    // Number of workers
    let num_workers = 4;

    // Master task
    let mut master = Task::new("master", "python")
        .with_arg("train_distributed.py".to_string())
        .with_arg("--role".to_string())
        .with_arg("master".to_string())
        .with_arg("--num-workers".to_string())
        .with_arg(num_workers.to_string())
        .with_priority(TaskPriority::High);

    master.resources = ResourceRequirements::new()
        .with_cpu(4.0)
        .with_memory(16.0);

    let master_id = scheduler.submit(master).await?;
    println!("? Master task: {}", master_id);

    // Worker tasks
    let mut worker_ids = Vec::new();
    for i in 0..num_workers {
        let mut worker = Task::new(
            &format!("worker-{}", i),
            "python"
        )
            .with_arg("train_distributed.py".to_string())
            .with_arg("--role".to_string())
            .with_arg("worker".to_string())
            .with_arg("--worker-id".to_string())
            .with_arg(i.to_string())
            .with_arg("--master-addr".to_string())
            .with_arg("master:8080".to_string())
            .with_dependency(master_id); // Wait for master

        worker.resources = ResourceRequirements::new()
            .with_cpu(2.0)
            .with_memory(8.0)
            .with_gpu(1);

        let worker_id = scheduler.submit(worker).await?;
        worker_ids.push(worker_id);
        println!("? Worker {} task: {}", i, worker_id);
    }

    println!("\n?? Distributed Setup:");
    println!("  1 Master + {} Workers", num_workers);
    println!("  Total GPUs: {}", num_workers);
    println!("  Workers wait for master to start");

    Ok(())
}
