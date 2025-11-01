//! Horovod Distributed Training Example
//!
//! SkyPilot: Multi-node training with Horovod

use styx_core::{SkyTask, SkyResources, launch};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Python equivalent:
    // import sky
    // 
    // task = sky.Task(
    //     name='horovod',
    //     num_nodes=2,
    //     setup='pip install horovod[tensorflow]',
    //     run='horovodrun -np 2 python train_tf.py'
    // )

    let task = SkyTask::new()
        .with_name("horovod-distributed")
        .with_num_nodes(2)
        .with_setup(r#"
            pip install tensorflow==2.12.0
            HOROVOD_WITH_TENSORFLOW=1 pip install horovod[tensorflow]
        "#)
        .with_run(r#"
            # 2 nodes x 1 GPU = 2 total GPUs
            horovodrun -np 2 \
              -H localhost:1,worker-node:1 \
              python train_tf.py
        "#);

    let resources = SkyResources::new()
        .with_cloud("aws")
        .with_accelerator("V100", 1);

    let task = task.with_resources(resources);

    let task_id = launch(task, Some("horovod-cluster".to_string()), false).await?;

    println!("? Horovod training launched: {}", task_id);
    println!("   Framework: Horovod");
    println!("   Nodes: 2");
    println!("   GPUs: 2 (1 per node)");
    println!("   Backend: TensorFlow");

    Ok(())
}
