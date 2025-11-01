//! GCP Example
//!
//! SkyPilot: Using Google Cloud Platform

use styx_core::{SkyTask, SkyResources, launch};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // YAML equivalent:
    // name: gcp-training
    // 
    // resources:
    //   cloud: gcp
    //   region: us-central1
    //   instance_type: n1-standard-8
    //   accelerators: T4:1
    // 
    // run: |
    //   python train.py

    let task = SkyTask::new()
        .with_name("gcp-training")
        .with_run("python train.py");

    let resources = SkyResources::new()
        .with_cloud("gcp")
        .with_region("us-central1")
        .with_instance_type("n1-standard-8")
        .with_accelerator("T4", 1);

    let task = task.with_resources(resources);

    let task_id = launch(task, None, false).await?;

    println!("? GCP task launched: {}", task_id);
    println!("   Cloud: GCP");
    println!("   Region: us-central1");
    println!("   Instance: n1-standard-8");
    println!("   GPU: 1x T4");

    Ok(())
}
