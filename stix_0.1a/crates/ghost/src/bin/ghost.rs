//! Ghost CLI Binary
//!
//! Usage:
//!   ghost create my-cave --image python:3.11 --cpu 2 --memory 1024
//!   ghost exec <cave-id> "print('Hello!')" --language python
//!   ghost list
//!   ghost stop <cave-id>
//!   ghost destroy <cave-id>

use styx_ghost::cli::Cli;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Initialize tracing
    tracing_subscriber::fmt::init();

    // Run CLI
    Cli::run().await
}
