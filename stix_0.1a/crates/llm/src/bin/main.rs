//! Styx LLM CLI binary

use styx_llm::cli::run_cli;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Initialize tracing
    tracing_subscriber::fmt::init();

    // Run CLI
    run_cli().await?;

    Ok(())
}
