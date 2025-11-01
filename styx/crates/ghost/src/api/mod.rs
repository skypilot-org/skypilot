//! REST API for Ghost Caves
//!
//! Core API Endpoints:
//! - /v1/sandbox - Get sandbox info
//! - /v1/shell/exec - Execute shell commands
//! - /v1/file/read - Read file content
//! - /v1/file/write - Write file content
//! - /v1/browser/screenshot - Take screenshot
//! - /v1/jupyter/execute - Execute Jupyter code

pub mod handlers;
pub mod routes;
pub mod models;

use axum::Router;
use std::sync::Arc;
use crate::ghost::Ghost;

/// API Server
pub struct ApiServer {
    ghost: Arc<Ghost>,
    port: u16,
}

impl ApiServer {
    /// Create new API server
    pub async fn new(port: u16) -> anyhow::Result<Self> {
        Ok(Self {
            ghost: Arc::new(Ghost::new().await?),
            port,
        })
    }

    /// Start API server
    pub async fn start(self) -> anyhow::Result<()> {
        let app = routes::create_router(self.ghost);

        let addr = format!("0.0.0.0:{}", self.port);
        println!("?? API Server starting on {}...", addr);

        let listener = tokio::net::TcpListener::bind(&addr).await?;
        
        println!("   ? API Server running!");
        println!("      API docs: http://localhost:{}/docs", self.port);

        axum::serve(listener, app).await?;

        Ok(())
    }
}
