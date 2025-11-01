//! Heartbeat service

use tracing::debug;

/// Heartbeat service
pub struct HeartbeatService {
    server_url: String,
}

impl HeartbeatService {
    /// Create new heartbeat service
    pub fn new(server_url: String) -> Self {
        Self { server_url }
    }

    /// Get server URL
    pub fn server_url(&self) -> &str {
        &self.server_url
    }

    /// Send heartbeat to server
    pub async fn send(&self) -> anyhow::Result<()> {
        debug!("Sending heartbeat to {}", self.server_url);
        // TODO: Implement HTTP POST to server
        Ok(())
    }
}
