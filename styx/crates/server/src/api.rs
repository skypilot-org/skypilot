//! API server implementation

use axum::Router;
use std::net::SocketAddr;

/// API server
pub struct ApiServer {
    addr: SocketAddr,
}

impl ApiServer {
    /// Create new API server
    pub fn new(addr: SocketAddr) -> Self {
        Self { addr }
    }

    /// Get server address
    pub fn addr(&self) -> SocketAddr {
        self.addr
    }

    /// Start server
    pub async fn serve(self) -> anyhow::Result<()> {
        // Will be implemented
        Ok(())
    }
}
