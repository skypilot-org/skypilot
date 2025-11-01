//! Jobs Queue Example
//!
//! SkyPilot: sky.jobs API

use styx_core::{SkyTask, JobQueue};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Python equivalent:
    // import sky
    // 
    // task = sky.Task(run='python train.py')
    // job_id = sky.jobs.submit(task, cluster='my-cluster')
    // 
    // # Check status
    // status = sky.jobs.status(job_id)
    // 
    // # Get logs
    // logs = sky.jobs.logs(job_id)
    // 
    // # List all jobs
    // all_jobs = sky.jobs.queue()

    let task = SkyTask::new()
        .with_name("training-job")
        .with_run("python train.py")
        .to_task();

    // Submit job
    let job_id = JobQueue::submit(task, "my-cluster".to_string()).await?;
    println!("? Job submitted: {}", job_id);

    // Check status
    let job = JobQueue::status(&job_id).await?;
    println!("   Status: {:?}", job.status);

    // Get logs
    let logs = JobQueue::logs(&job_id, false).await?;
    println!("   Logs: {} bytes", logs.len());

    // List all jobs
    let all_jobs = JobQueue::list(Some("my-cluster".to_string())).await?;
    println!("   Total jobs: {}", all_jobs.len());

    // Queue status
    let queue = JobQueue::queue().await?;
    println!("   Queue length: {}", queue.len());

    Ok(())
}
