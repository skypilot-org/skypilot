//! Autostop Example
//!
//! SkyPilot: Automatic cluster shutdown after idle

use styx_core::{SkyTask, SkyResources, launch};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // YAML equivalent:
    // name: autostop-example
    // 
    // resources:
    //   cloud: aws
    // 
    // run: |
    //   python train.py
    // 
    // # Auto-shutdown after 10 minutes idle
    // # sky launch task.yaml --down --idle-minutes 10

    let mut task = SkyTask::new()
        .with_name("autostop-example")
        .with_run("python train.py");

    let resources = SkyResources::new()
        .with_cloud("aws");

    task = task.with_resources(resources);

    // Note: Autostop would be configured via launch options
    // For now, demonstrate the concept
    task = task.with_env("STYX_AUTOSTOP_IDLE_MINUTES", "10");
    task = task.with_env("STYX_AUTOSTOP_DOWN", "true");

    let task_id = launch(task, Some("autostop-cluster".to_string()), false).await?;

    println!("? Task with autostop launched: {}", task_id);
    println!("   Cluster: autostop-cluster");
    println!("   Autostop: 10 minutes idle");
    println!("   Action: Terminate cluster");
    println!("\n?? Saves money by auto-terminating idle clusters");

    Ok(())
}
