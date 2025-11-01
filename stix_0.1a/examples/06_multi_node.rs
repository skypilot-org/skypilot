//! Multi-Node Training Example
//!
//! SkyPilot: Multi-node distributed training

use styx_core::{SkyTask, SkyResources, launch};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // YAML equivalent:
    // name: multi-node-train
    // 
    // num_nodes: 4
    // 
    // resources:
    //   accelerators: V100:8
    // 
    // setup: |
    //   pip install torch torchvision horovod
    // 
    // run: |
    //   horovodrun -np $((4 * 8)) \
    //     python train.py

    let task = SkyTask::new()
        .with_name("multi-node-train")
        .with_num_nodes(4)
        .with_setup("pip install torch torchvision horovod")
        .with_run(r#"
            # 4 nodes x 8 GPUs = 32 total GPUs
            horovodrun -np 32 python train.py
        "#);

    let resources = SkyResources::new()
        .with_accelerator("V100", 8);

    let task = task.with_resources(resources);

    let task_id = launch(task, None, false).await?;

    println!("? Multi-node training launched: {}", task_id);
    println!("   Nodes: 4");
    println!("   GPUs per node: 8");
    println!("   Total GPUs: 32");

    Ok(())
}
