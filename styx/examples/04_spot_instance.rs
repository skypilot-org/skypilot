//! Spot Instance Example
//!
//! SkyPilot: Using spot instances with use_spot

use styx_core::{SkyTask, SkyResources, SpotOps, SpotConfig};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // YAML equivalent:
    // name: spot-training
    // 
    // resources:
    //   accelerators: V100:1
    //   use_spot: true
    // 
    // run: |
    //   python train.py --epochs 100

    let task = SkyTask::new()
        .with_name("spot-training")
        .with_run("python train.py --epochs 100");

    let resources = SkyResources::new()
        .with_accelerator("V100", 1)
        .with_spot(true);

    let task = task.with_resources(resources);

    // Launch as spot job
    let config = SpotConfig {
        use_spot: true,
        max_price: Some(1.5),
        recovery: styx_core::RecoveryStrategy::Restart,
        max_retries: 3,
    };

    let job_id = SpotOps::launch(
        task.to_task(),
        Some("spot-training".to_string()),
        config
    ).await?;

    println!("? Spot job launched: {}", job_id);
    println!("   Recovery: Auto-restart on preemption");
    println!("   Max price: $1.50/hour");

    Ok(())
}
