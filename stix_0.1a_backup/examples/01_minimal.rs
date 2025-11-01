//! Minimal Example (minimal.yaml)
//! 
//! SkyPilot: examples/minimal.yaml
//! The simplest possible task

use styx_core::{SkyTask, SkyResources, launch};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // YAML equivalent:
    // name: minimal
    // 
    // resources:
    //   cloud: aws
    // 
    // run: |
    //   echo "Hello, SkyPilot!"
    //   conda env list

    let task = SkyTask::new()
        .with_name("minimal")
        .with_run(r#"
            echo "Hello, SkyPilot!"
            conda env list
        "#);

    let resources = SkyResources::new()
        .with_cloud("aws");

    let task = task.with_resources(resources);

    let task_id = launch(task, None, false).await?;

    println!("? Minimal task launched: {}", task_id);

    Ok(())
}
