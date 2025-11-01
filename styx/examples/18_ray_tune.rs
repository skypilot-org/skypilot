//! Ray Tune Example
//!
//! SkyPilot: Hyperparameter tuning with Ray Tune

use styx_core::{SkyTask, SkyResources, launch};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Python equivalent:
    // import sky
    // 
    // task = sky.Task(
    //     name='ray-tune',
    //     setup='pip install ray[tune]',
    //     run='python tune_mnist.py'
    // )

    let task = SkyTask::new()
        .with_name("ray-tune")
        .with_setup(r#"
            pip install 'ray[tune]' torch torchvision
            git clone https://github.com/ray-project/ray || true
        "#)
        .with_run(r#"
            cd ray/python/ray/tune/examples
            python mnist_pytorch.py --num-samples 10
        "#);

    let resources = SkyResources::new()
        .with_cpus(8.0)
        .with_memory(16.0)
        .with_accelerator("T4", 1);

    let task = task.with_resources(resources);

    let task_id = launch(task, Some("ray-tune".to_string()), false).await?;

    println!("? Ray Tune job launched: {}", task_id);
    println!("   Cluster: ray-tune");
    println!("   Features:");
    println!("     ? Hyperparameter tuning");
    println!("     ? 10 parallel trials");
    println!("     ? 1x T4 GPU");

    Ok(())
}
