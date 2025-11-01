//! GPU Task Example
//!
//! Submit a task that requires GPU resources.

use styx_core::{Scheduler, SchedulerConfig, Task, TaskPriority, ResourceRequirements};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let scheduler = Scheduler::new(SchedulerConfig::default());

    // Create task with GPU requirements
    let mut task = Task::new("gpu-training", "python")
        .with_arg("train_llm.py".to_string())
        .with_arg("--model".to_string())
        .with_arg("gpt2".to_string())
        .with_priority(TaskPriority::Critical);

    // Set GPU requirements
    task.resources = ResourceRequirements::new()
        .with_cpu(8.0)
        .with_memory(32.0)
        .with_gpu(2) // 2 GPUs
        .with_gpu_type("nvidia-v100");

    // Submit task
    let task_id = scheduler.submit(task).await?;

    println!("? GPU Task submitted: {}", task_id);
    println!("\n?? Task details:");
    println!("  Command: python train_llm.py --model gpt2");
    println!("  CPU: 8 cores");
    println!("  Memory: 32 GB");
    println!("  GPU: 2x nvidia-v100");
    println!("  Priority: Critical");

    Ok(())
}
