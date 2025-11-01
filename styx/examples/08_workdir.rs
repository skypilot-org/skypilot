//! Working Directory Example
//!
//! SkyPilot: Setting working directory

use styx_core::{SkyTask, launch};
use std::path::PathBuf;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // YAML equivalent:
    // name: workdir-example
    // 
    // workdir: /tmp/myproject
    // 
    // file_mounts:
    //   /tmp/myproject: .
    // 
    // run: |
    //   ls -la
    //   python train.py

    let task = SkyTask::new()
        .with_name("workdir-example")
        .with_workdir(PathBuf::from("/tmp/myproject"))
        .with_file_mount(
            PathBuf::from("/tmp/myproject"),
            PathBuf::from(".")
        )
        .with_run(r#"
            ls -la
            python train.py
        "#);

    let task_id = launch(task, None, false).await?;

    println!("? Task with workdir launched: {}", task_id);
    println!("   Working directory: /tmp/myproject");

    Ok(())
}
