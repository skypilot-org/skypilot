//! Styx Agent - Remote execution daemon

use std::time::Duration;
use tokio::time;
use tracing::{info, Level};
use tracing_subscriber;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Setup logging
    tracing_subscriber::fmt()
        .with_max_level(Level::INFO)
        .with_target(false)
        .init();

    info!("?? Styx Agent {} starting...", styx_agent::VERSION);

    // Initialize system monitor
    let system_monitor = styx_agent::SystemMonitor::new();

    // Initialize heartbeat service
    let heartbeat = styx_agent::HeartbeatService::new(
        std::env::var("STYX_SERVER_URL").unwrap_or_else(|_| "http://localhost:8080".to_string()),
    );

    // Initialize task executor
    let executor = styx_agent::TaskExecutor::new();

    info!("? Agent initialized");
    info!(
        "   System: {} cores, {:.2}GB RAM",
        system_monitor.cpu_count(),
        system_monitor.total_memory_gb()
    );
    info!("   Server: {}", heartbeat.server_url());

    // Main agent loop
    let mut interval = time::interval(Duration::from_secs(30));

    loop {
        interval.tick().await;

        // Send heartbeat
        if let Err(e) = heartbeat.send().await {
            tracing::warn!("Failed to send heartbeat: {}", e);
        }

        // Check for new tasks
        if let Err(e) = executor.poll_tasks().await {
            tracing::warn!("Failed to poll tasks: {}", e);
        }

        // Update system metrics
        system_monitor.update();
    }
}
