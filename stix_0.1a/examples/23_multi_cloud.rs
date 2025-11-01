//! Multi-Cloud Example
//!
//! SkyPilot: Try multiple clouds in order

use styx_core::{SkyTask, SkyResources, launch};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // YAML equivalent:
    // name: multi-cloud
    // 
    // resources:
    //   # Try clouds in order
    //   cloud: [aws, gcp, azure]
    //   accelerators: V100:1
    // 
    // run: |
    //   python train.py

    let task = SkyTask::new()
        .with_name("multi-cloud")
        .with_run("python train.py");

    // Try AWS first
    println!("?? Multi-Cloud Launch Strategy:\n");

    let clouds = vec!["aws", "gcp", "azure"];
    
    for (i, cloud) in clouds.iter().enumerate() {
        println!("{}. Trying {}...", i + 1, cloud);
        
        let resources = SkyResources::new()
            .with_cloud(*cloud)
            .with_accelerator("V100", 1);

        let task_clone = task.clone().with_resources(resources);

        match launch(task_clone, None, false).await {
            Ok(task_id) => {
                println!("   ? Launched on {}: {}", cloud, task_id);
                println!("\n?? Successfully launched on: {}", cloud);
                return Ok(());
            }
            Err(e) => {
                println!("   ? Failed on {}: {}", cloud, e);
                continue;
            }
        }
    }

    println!("\n? All clouds failed");
    Err(anyhow::anyhow!("No cloud available"))
}
