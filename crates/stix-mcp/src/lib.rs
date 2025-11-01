mod agent;
mod locks;
mod logger;
mod tools;
mod server;
mod admin;
pub mod client;

pub use agent::{Agent, Permission};
pub use locks::{LockManager, FileLock};
pub use logger::{AuditLogger, LogEntry};
pub use tools::{
    ToolExecutor, ToolRequest, ToolResponse,
    LockFileParams, UnlockFileParams, UpdateProgressParams,
    IssueTaskParams, SearchCodebaseParams,
};
pub use server::{ServerState, create_router};
pub use admin::create_admin_router;
pub use client::McpClient;

use anyhow::Result;
use std::path::PathBuf;
use std::sync::Arc;
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt};

/// Configuration for the MCP server
#[derive(Debug, Clone)]
pub struct ServerConfig {
    pub host: String,
    pub port: u16,
    pub workspace_root: PathBuf,
    pub log_file: Option<PathBuf>,
    pub max_requests_per_minute: u32,
}

impl Default for ServerConfig {
    fn default() -> Self {
        Self {
            host: "0.0.0.0".to_string(),
            port: 8080,
            workspace_root: PathBuf::from("."),
            log_file: Some(PathBuf::from("mcp-audit.log")),
            max_requests_per_minute: 100,
        }
    }
}

/// Start the MCP server
pub async fn start_server(config: ServerConfig) -> Result<()> {
    // Initialize tracing
    tracing_subscriber::registry()
        .with(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "stix_mcp=debug,tower_http=debug".into()),
        )
        .with(tracing_subscriber::fmt::layer())
        .init();

    tracing::info!("Starting STIX MCP Server");
    tracing::info!("Workspace root: {:?}", config.workspace_root);
    tracing::info!("Log file: {:?}", config.log_file);
    tracing::info!("Rate limit: {} requests/minute", config.max_requests_per_minute);

    // Create server state
    let state = Arc::new(ServerState::new(
        config.workspace_root,
        config.log_file,
    ));

    // Create main router
    let app = create_router(state.clone());
    
    // Create admin router and nest it
    let admin_router = create_admin_router(state.clone());
    let app = app.nest("/admin", admin_router);

    // Bind server
    let addr = format!("{}:{}", config.host, config.port);
    let listener = tokio::net::TcpListener::bind(&addr).await?;
    
    tracing::info!("Server listening on {}", addr);
    tracing::info!("Admin UI will be available at http://{}/admin", addr);
    
    // Start server
    axum::serve(listener, app).await?;

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_default_config() {
        let config = ServerConfig::default();
        assert_eq!(config.port, 8080);
        assert_eq!(config.max_requests_per_minute, 100);
    }
}
