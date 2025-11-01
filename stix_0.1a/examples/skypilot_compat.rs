//! SkyPilot Compatibility Example
//!
//! Shows 1:1 Python ? Rust API compatibility

use styx_core::{SkyTask, SkyResources, launch};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    println!("?? SkyPilot ? Styx Compatibility Demo\n");

    // Python equivalent:
    // task = sky.Task(
    //     setup='pip install torch',
    //     run='python train.py',
    //     num_nodes=2
    // )
    let task = SkyTask::new()
        .with_name("training")
        .with_setup("pip install torch torchvision")
        .with_run("python train.py --epochs 100")
        .with_num_nodes(2);

    println!("? Task created:");
    println!("   Name: {:?}", task.name);
    println!("   Setup: {:?}", task.setup);
    println!("   Run: {:?}", task.run);
    println!("   Nodes: {}", task.num_nodes);

    // Python equivalent:
    // task.set_resources(sky.Resources(
    //     cloud='aws',
    //     instance_type='p3.2xlarge',
    //     accelerators='V100:1'
    // ))
    let resources = SkyResources::new()
        .with_cloud("aws")
        .with_instance_type("p3.2xlarge")
        .with_cpus(8.0)
        .with_memory(61.0)
        .with_accelerator("V100", 1)
        .with_spot(true);

    println!("\n? Resources configured:");
    println!("   Cloud: {:?}", resources.cloud);
    println!("   Instance: {:?}", resources.instance_type);
    println!("   CPUs: {:?}", resources.cpus);
    println!("   Memory: {:?} GB", resources.memory);
    println!("   GPUs: {:?}", resources.accelerators);
    println!("   Spot: {}", resources.use_spot);

    let task_with_resources = task.with_resources(resources);

    // Python equivalent:
    // sky.launch(task, cluster_name='my-cluster')
    let task_id = launch(
        task_with_resources,
        Some("my-cluster".to_string()),
        false
    ).await?;

    println!("\n?? Task launched!");
    println!("   Task ID: {}", task_id);
    println!("   Cluster: my-cluster");

    println!("\n?? Python equivalent:");
    println!("   task = sky.Task(");
    println!("       setup='pip install torch torchvision',");
    println!("       run='python train.py --epochs 100',");
    println!("       num_nodes=2");
    println!("   )");
    println!("   task.set_resources(sky.Resources(");
    println!("       cloud='aws',");
    println!("       instance_type='p3.2xlarge',");
    println!("       accelerators='V100:1',");
    println!("       use_spot=True");
    println!("   ))");
    println!("   sky.launch(task, cluster_name='my-cluster')");

    Ok(())
}
