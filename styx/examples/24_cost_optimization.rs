//! Cost Optimization Example
//!
//! SkyPilot: Optimize for lowest cost

use styx_core::{SkyTask, SkyResources, launch};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Python equivalent:
    // import sky
    // 
    // # SkyPilot automatically finds cheapest option
    // task = sky.Task(
    //     name='cost-optimized',
    //     run='python train.py'
    // )
    // 
    // # Try spot first, then on-demand
    // task.set_resources(sky.Resources(
    //     accelerators='V100:1',
    //     use_spot=True  # 70% cheaper!
    // ))

    let task = SkyTask::new()
        .with_name("cost-optimized")
        .with_run("python train.py --epochs 100");

    // Strategy 1: Try spot instances first (70% cheaper)
    let spot_resources = SkyResources::new()
        .with_accelerator("V100", 1)
        .with_spot(true);

    println!("?? Cost Optimization Strategy:\n");
    println!("1. Trying spot instances (70% cheaper)...");

    let task_with_spot = task.clone().with_resources(spot_resources);

    match launch(task_with_spot, None, false).await {
        Ok(task_id) => {
            println!("   ? Launched on spot: {}", task_id);
            println!("\n?? Estimated savings: ~70% vs on-demand");
            return Ok(());
        }
        Err(e) => {
            println!("   ??  Spot unavailable: {}", e);
        }
    }

    // Strategy 2: Fall back to on-demand
    println!("\n2. Falling back to on-demand...");
    
    let ondemand_resources = SkyResources::new()
        .with_accelerator("V100", 1)
        .with_spot(false);

    let task_ondemand = task.with_resources(ondemand_resources);

    let task_id = launch(task_ondemand, None, false).await?;
    println!("   ? Launched on-demand: {}", task_id);

    Ok(())
}
