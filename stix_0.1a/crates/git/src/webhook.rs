//! Webhook management

use reqwest::Client;
use serde::{Deserialize, Serialize};

/// Webhook model
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Webhook {
    pub id: i64,
    pub repo_id: i64,
    pub url: String,
    pub secret: String,
    pub events: Vec<WebhookEvent>,
    pub is_active: bool,
    pub created_at: chrono::DateTime<chrono::Utc>,
}

/// Webhook events
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum WebhookEvent {
    Push,
    PullRequest,
    Issue,
    Release,
    Fork,
    Watch,
}

/// Webhook payload
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WebhookPayload {
    pub event: WebhookEvent,
    pub repository: String,
    pub sender: String,
    pub data: serde_json::Value,
}

/// Webhook delivery
pub struct WebhookDelivery {
    client: Client,
}

impl WebhookDelivery {
    pub fn new() -> Self {
        Self {
            client: Client::new(),
        }
    }

    /// Deliver webhook
    pub async fn deliver(&self, webhook: &Webhook, payload: WebhookPayload) -> anyhow::Result<()> {
        let response = self
            .client
            .post(&webhook.url)
            .header("X-Styx-Event", format!("{:?}", payload.event))
            .header("X-Styx-Signature", self.sign(&webhook.secret, &payload)?)
            .json(&payload)
            .send()
            .await?;

        if !response.status().is_success() {
            anyhow::bail!("Webhook delivery failed: {}", response.status());
        }

        Ok(())
    }

    fn sign(&self, secret: &str, payload: &WebhookPayload) -> anyhow::Result<String> {
        use sha2::{Digest, Sha256};
        let payload_json = serde_json::to_string(payload)?;
        let mut hasher = Sha256::new();
        hasher.update(secret.as_bytes());
        hasher.update(payload_json.as_bytes());
        Ok(format!("sha256={}", hex::encode(hasher.finalize())))
    }
}
