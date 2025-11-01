use stix_mcp::{start_server, ServerConfig};
use anyhow::Result;
use std::path::PathBuf;

#[tokio::main]
async fn main() -> Result<()> {
    // Load configuration from environment or use defaults
    let config = ServerConfig {
        host: std::env::var("MCP_HOST").unwrap_or_else(|_| "0.0.0.0".to_string()),
        port: std::env::var("MCP_PORT")
            .ok()
            .and_then(|p| p.parse().ok())
            .unwrap_or(8080),
        workspace_root: std::env::var("MCP_WORKSPACE_ROOT")
            .map(PathBuf::from)
            .unwrap_or_else(|_| PathBuf::from(".")),
        log_file: std::env::var("MCP_LOG_FILE")
            .ok()
            .map(PathBuf::from)
            .or(Some(PathBuf::from("mcp-audit.log"))),
        max_requests_per_minute: std::env::var("MCP_RATE_LIMIT")
            .ok()
            .and_then(|r| r.parse().ok())
            .unwrap_or(100),
    };

    start_server(config).await
}
