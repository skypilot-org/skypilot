//! Grid Search Example
//!
//! SkyPilot: Hyperparameter grid search

use styx_core::{Scheduler, SchedulerConfig, Task, TaskPriority};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Python equivalent:
    // import sky
    // 
    // for lr in [0.001, 0.01, 0.1]:
    //     for bs in [32, 64, 128]:
    //         task = sky.Task(
    //             name=f'grid-lr{lr}-bs{bs}',
    //             run=f'python train.py --lr {lr} --batch-size {bs}'
    //         )
    //         sky.launch(task)

    let scheduler = Scheduler::new(SchedulerConfig::default());

    let learning_rates = vec![0.001, 0.01, 0.1];
    let batch_sizes = vec![32, 64, 128];

    println!("?? Grid Search: {} experiments\n", learning_rates.len() * batch_sizes.len());

    let mut submitted = 0;

    for lr in &learning_rates {
        for bs in &batch_sizes {
            let task_name = format!("grid-lr{}-bs{}", lr, bs);
            
            let task = Task::new(&task_name, "python")
                .with_arg("train.py".to_string())
                .with_arg("--lr".to_string())
                .with_arg(lr.to_string())
                .with_arg("--batch-size".to_string())
                .with_arg(bs.to_string())
                .with_priority(TaskPriority::Normal);

            let task_id = scheduler.submit(task).await?;
            submitted += 1;

            if submitted <= 3 {
                println!("? {} -> {}", task_name, task_id);
            }
        }
    }

    println!("   ... and {} more", submitted - 3);
    println!("\n?? Grid Search Summary:");
    println!("   Learning rates: {:?}", learning_rates);
    println!("   Batch sizes: {:?}", batch_sizes);
    println!("   Total experiments: {}", submitted);

    let stats = scheduler.stats().await;
    println!("\n?? Scheduler stats:");
    println!("   Pending: {}", stats.pending);

    Ok(())
}
