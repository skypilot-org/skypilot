//! Multi-GPU Training Example (multi_accelerators.yaml)
//!
//! SkyPilot: examples/multi_accelerators.yaml

use styx_core::{SkyTask, SkyResources, launch};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // YAML equivalent:
    // name: multi-gpu
    // 
    // resources:
    //   accelerators: V100:4
    //   cloud: aws
    // 
    // setup: |
    //   pip install torch torchvision
    // 
    // run: |
    //   python -m torch.distributed.launch \
    //     --nproc_per_node=4 \
    //     train.py

    let task = SkyTask::new()
        .with_name("multi-gpu")
        .with_setup("pip install torch torchvision")
        .with_run(r#"
            python -m torch.distributed.launch \
              --nproc_per_node=4 \
              train.py
        "#);

    let resources = SkyResources::new()
        .with_cloud("aws")
        .with_accelerator("V100", 4);

    let task = task.with_resources(resources);

    let task_id = launch(task, None, false).await?;

    println!("? Multi-GPU training launched: {}", task_id);
    println!("   Using 4x V100 GPUs");

    Ok(())
}
