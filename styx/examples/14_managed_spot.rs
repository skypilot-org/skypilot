//! Managed Spot Job Example (managed_spot.yaml)
//!
//! SkyPilot: examples/managed_spot.yaml
//! Spot instances with automatic recovery

use styx_core::{SkyTask, SkyResources, SpotOps, SpotConfig, RecoveryStrategy};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // YAML equivalent:
    // name: managed-spot
    // 
    // resources:
    //   accelerators: V100:1
    //   use_spot: true
    // 
    // setup: |
    //   pip install torch
    // 
    // run: |
    //   python train.py --epochs 100 --checkpoint-dir /checkpoint

    let task = SkyTask::new()
        .with_name("managed-spot")
        .with_setup("pip install torch")
        .with_run("python train.py --epochs 100 --checkpoint-dir /checkpoint");

    let resources = SkyResources::new()
        .with_accelerator("V100", 1)
        .with_spot(true);

    let task = task.with_resources(resources).to_task();

    // Launch with spot config
    let spot_config = SpotConfig {
        use_spot: true,
        max_price: Some(1.0), // Max $1/hour
        recovery: RecoveryStrategy::Restart,
        max_retries: 5,
    };

    let job_id = SpotOps::launch(
        task,
        Some("managed-spot-job".to_string()),
        spot_config
    ).await?;

    println!("? Managed spot job launched: {}", job_id);
    println!("   Features:");
    println!("     ? Automatic recovery on preemption");
    println!("     ? Checkpoint-based resume");
    println!("     ? Max retries: 5");
    println!("     ? Max price: $1.00/hour");
    println!("\n?? Cost savings: ~70% vs on-demand");

    Ok(())
}
