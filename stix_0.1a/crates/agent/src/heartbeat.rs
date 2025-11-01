//! Heartbeat service

use chrono::Utc;
use serde_json::json;
use tracing::debug;

/// Heartbeat service
pub struct HeartbeatService {
    server_url: String,
    client: reqwest::Client,
}

impl HeartbeatService {
    /// Create new heartbeat service
    pub fn new(server_url: String) -> Self {
        Self {
            server_url,
            client: reqwest::Client::new(),
        }
    }

    /// Get server URL
    pub fn server_url(&self) -> &str {
        &self.server_url
    }

    fn heartbeat_endpoint(&self) -> String {
        format!(
            "{}/api/v1/agents/heartbeat",
            self.server_url.trim_end_matches('/')
        )
    }

    /// Send heartbeat to server
    pub async fn send(&self) -> anyhow::Result<()> {
        let payload = json!({
            "timestamp": Utc::now(),
            "agent": "styx-agent",
            "version": crate::VERSION,
        });

        let url = self.heartbeat_endpoint();
        debug!("Sending heartbeat to {}", url);

        let response = self.client.post(&url).json(&payload).send().await;
        match response {
            Ok(resp) if resp.status().is_success() => Ok(()),
            Ok(resp) => {
                debug!(status = ?resp.status(), "Heartbeat not acknowledged");
                Ok(())
            }
            Err(err) => {
                debug!(?err, "Heartbeat request failed");
                Err(err.into())
            }
        }
    }
}
