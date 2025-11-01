use serde::{Deserialize, Serialize};
use time::OffsetDateTime;

/// Database row for tasks table
#[derive(Debug, Clone, Serialize, Deserialize, sqlx::FromRow)]
pub struct TaskRow {
    pub id: String,
    pub name: String,
    pub status: String,
    pub retries: i32,
    pub max_retries: i32,
    pub payload: Option<Vec<u8>>,
    pub created_at: String,
    pub updated_at: String,
}

/// Database row for edges table
#[derive(Debug, Clone, Serialize, Deserialize, sqlx::FromRow)]
pub struct EdgeRow {
    pub src: String,
    pub dst: String,
}

/// Database row for kv table
#[derive(Debug, Clone, Serialize, Deserialize, sqlx::FromRow)]
pub struct KvRow {
    pub k: String,
    pub v: Vec<u8>,
    pub updated_at: String,
}

/// Convert OffsetDateTime to RFC3339 string
pub fn datetime_to_string(dt: OffsetDateTime) -> String {
    dt.format(&time::format_description::well_known::Rfc3339)
        .unwrap_or_else(|_| dt.to_string())
}

/// Parse RFC3339 string to OffsetDateTime
pub fn string_to_datetime(s: &str) -> Result<OffsetDateTime, time::error::Parse> {
    OffsetDateTime::parse(s, &time::format_description::well_known::Rfc3339)
}
