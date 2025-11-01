//! Docker Run Example
//!
//! SkyPilot: Running containerized applications

use styx_core::{SkyTask, SkyResources, launch};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // YAML equivalent:
    // name: docker-app
    // 
    // resources:
    //   image_id: docker:pytorch/pytorch:1.9.0-cuda11.1-cudnn8-runtime
    // 
    // run: |
    //   python train.py

    let task = SkyTask::new()
        .with_name("docker-app")
        .with_env("STYX_IMAGE", "docker:pytorch/pytorch:1.9.0-cuda11.1-cudnn8-runtime")
        .with_run(r#"
            # Commands run inside Docker container
            python train.py
        "#);

    let resources = SkyResources::new()
        .with_accelerator("V100", 1);

    let task = task.with_resources(resources);

    let task_id = launch(task, None, false).await?;

    println!("? Docker task launched: {}", task_id);
    println!("   Image: pytorch/pytorch:1.9.0-cuda11.1-cudnn8-runtime");
    println!("   GPU: 1x V100");
    println!("\n?? Benefits:");
    println!("     ? Reproducible environment");
    println!("     ? No setup time");
    println!("     ? Portable");

    Ok(())
}
