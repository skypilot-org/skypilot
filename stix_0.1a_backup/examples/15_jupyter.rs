//! Jupyter Lab Example (jupyter_lab.yaml)
//!
//! SkyPilot: examples/jupyter_lab.yaml
//! Launch Jupyter Lab on cloud

use styx_core::{SkyTask, SkyResources, launch};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // YAML equivalent:
    // name: jupyter
    // 
    // resources:
    //   cpus: 4+
    //   memory: 8+
    // 
    // setup: |
    //   pip install jupyterlab
    //   jupyter lab --generate-config
    //   python -c "from jupyter_server.auth import passwd; print(passwd('sky'))" > /tmp/jupyter_hash
    // 
    // run: |
    //   jupyter lab --ip=0.0.0.0 --port=8888 --no-browser \
    //     --NotebookApp.password=$(cat /tmp/jupyter_hash)

    let task = SkyTask::new()
        .with_name("jupyter")
        .with_setup(r#"
            pip install jupyterlab
            jupyter lab --generate-config
            python -c "from jupyter_server.auth import passwd; print(passwd('sky'))" > /tmp/jupyter_hash
        "#)
        .with_run(r#"
            jupyter lab --ip=0.0.0.0 --port=8888 --no-browser \
              --NotebookApp.password=$(cat /tmp/jupyter_hash)
        "#);

    let resources = SkyResources::new()
        .with_cpus(4.0)
        .with_memory(8.0);

    let task = task.with_resources(resources);

    let task_id = launch(task, Some("jupyter".to_string()), false).await?;

    println!("? Jupyter Lab launched: {}", task_id);
    println!("   Cluster: jupyter");
    println!("   Access: http://<cluster-ip>:8888");
    println!("   Password: sky");
    println!("\n?? Get cluster IP:");
    println!("   styx status jupyter");

    Ok(())
}
