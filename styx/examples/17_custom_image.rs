//! Custom Image Example
//!
//! SkyPilot: Using custom Docker images

use styx_core::{SkyTask, SkyResources, launch};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // YAML equivalent:
    // name: custom-image
    // 
    // resources:
    //   image_id: docker:myusername/myimage:latest
    //   cloud: aws
    // 
    // run: |
    //   python train.py

    let task = SkyTask::new()
        .with_name("custom-image")
        .with_env("STYX_IMAGE", "docker:myusername/myimage:latest")
        .with_run("python train.py");

    let resources = SkyResources::new()
        .with_cloud("aws");

    let task = task.with_resources(resources);

    let task_id = launch(task, None, false).await?;

    println!("? Task with custom image launched: {}", task_id);
    println!("   Image: myusername/myimage:latest");
    println!("   Cloud: AWS");
    println!("\n?? Custom images allow pre-configured environments");

    Ok(())
}
