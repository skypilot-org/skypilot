//! Database models

use serde::{Deserialize, Serialize};
use sqlx::FromRow;

/// Task record in database
#[derive(Debug, Clone, Serialize, Deserialize, FromRow)]
pub struct TaskRecord {
    pub id: String,
    pub name: String,
    pub command: String,
    pub args: String, // JSON-encoded Vec<String>
    pub status: String,
    pub priority: i32,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub started_at: Option<chrono::DateTime<chrono::Utc>>,
    pub completed_at: Option<chrono::DateTime<chrono::Utc>>,
    pub error: Option<String>,
}

/// Instance record in database
#[derive(Debug, Clone, Serialize, Deserialize, FromRow)]
pub struct InstanceRecord {
    pub id: String,
    pub name: String,
    pub provider: String,
    pub instance_type: String,
    pub state: String,
    pub region: String,
    pub public_ip: Option<String>,
    pub private_ip: Option<String>,
    pub created_at: chrono::DateTime<chrono::Utc>,
}

/// Job record in database
#[derive(Debug, Clone, Serialize, Deserialize, FromRow)]
pub struct JobRecord {
    pub id: String,
    pub name: String,
    pub status: String,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub completed_at: Option<chrono::DateTime<chrono::Utc>>,
}
