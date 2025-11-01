//! Kubernetes Example
//!
//! SkyPilot: Running on Kubernetes

use styx_core::{SkyTask, SkyResources, launch};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // YAML equivalent:
    // name: k8s-job
    // 
    // resources:
    //   cloud: kubernetes
    //   cpus: 4+
    //   memory: 16+
    // 
    // run: |
    //   python train.py

    let task = SkyTask::new()
        .with_name("k8s-job")
        .with_run("python train.py");

    let resources = SkyResources::new()
        .with_cloud("kubernetes")
        .with_cpus(4.0)
        .with_memory(16.0);

    let task = task.with_resources(resources);

    let task_id = launch(task, None, false).await?;

    println!("? Kubernetes job launched: {}", task_id);
    println!("   Cloud: Kubernetes");
    println!("   CPUs: 4");
    println!("   Memory: 16 GB");

    Ok(())
}
