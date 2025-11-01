//! Azure Example
//!
//! SkyPilot: Running on Microsoft Azure

use styx_core::{SkyTask, SkyResources, launch};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // YAML equivalent:
    // name: azure-training
    // 
    // resources:
    //   cloud: azure
    //   region: westus2
    //   instance_type: Standard_NC6s_v3
    //   accelerators: V100:1
    // 
    // run: |
    //   python train.py

    let task = SkyTask::new()
        .with_name("azure-training")
        .with_run("python train.py");

    let resources = SkyResources::new()
        .with_cloud("azure")
        .with_region("westus2")
        .with_instance_type("Standard_NC6s_v3")
        .with_accelerator("V100", 1);

    let task = task.with_resources(resources);

    let task_id = launch(task, None, false).await?;

    println!("? Azure task launched: {}", task_id);
    println!("   Cloud: Azure");
    println!("   Region: westus2");
    println!("   Instance: Standard_NC6s_v3");
    println!("   GPU: 1x V100");

    Ok(())
}
