//! File Mounts Example
//!
//! SkyPilot: File mounting with file_mounts

use styx_core::{SkyTask, launch};
use std::path::PathBuf;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // YAML equivalent:
    // name: file-mounts
    // 
    // file_mounts:
    //   /remote/data: ~/local/data
    //   /remote/model: s3://my-bucket/model.pth
    // 
    // run: |
    //   ls /remote/data
    //   python train.py --data /remote/data --model /remote/model

    let task = SkyTask::new()
        .with_name("file-mounts")
        .with_file_mount(
            PathBuf::from("/remote/data"),
            PathBuf::from("~/local/data")
        )
        .with_file_mount(
            PathBuf::from("/remote/model"),
            PathBuf::from("s3://my-bucket/model.pth")
        )
        .with_run(r#"
            ls /remote/data
            python train.py --data /remote/data --model /remote/model
        "#);

    let task_id = launch(task, None, false).await?;

    println!("? Task with file mounts launched: {}", task_id);
    println!("   Mounted: ~/local/data ? /remote/data");
    println!("   Mounted: s3://my-bucket/model.pth ? /remote/model");

    Ok(())
}
