//! Managed Job Example (managed_job.yaml)
//!
//! SkyPilot: examples/managed_job.yaml
//! Managed jobs with automatic recovery

use styx_core::{SkyTask, SkyResources, JobQueue};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // YAML equivalent:
    // name: managed-job
    // 
    // resources:
    //   accelerators: V100:1
    // 
    // setup: |
    //   pip install torch
    // 
    // run: |
    //   python train.py --epochs 100

    let task = SkyTask::new()
        .with_name("managed-job")
        .with_setup("pip install torch")
        .with_run("python train.py --epochs 100");

    let resources = SkyResources::new()
        .with_accelerator("V100", 1);

    let task = task.with_resources(resources).to_task();

    // Submit as managed job (automatic recovery)
    let job_id = JobQueue::submit(task, "managed-cluster".to_string()).await?;

    println!("? Managed job submitted: {}", job_id);
    println!("   Cluster: managed-cluster");
    println!("   Features:");
    println!("     ? Automatic recovery on failure");
    println!("     ? Job queue management");
    println!("     ? Status tracking");

    // Check status
    let status = JobQueue::status(&job_id).await?;
    println!("\n?? Job status: {:?}", status.status);

    Ok(())
}
