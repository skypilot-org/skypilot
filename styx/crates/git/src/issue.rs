//! Issue tracking

use serde::{Deserialize, Serialize};

/// Issue model
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Issue {
    pub id: i64,
    pub repo_id: i64,
    pub index: i64,
    pub title: String,
    pub content: String,
    pub author_id: i64,
    pub assignee_id: Option<i64>,
    pub milestone_id: Option<i64>,
    pub is_closed: bool,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
    pub closed_at: Option<chrono::DateTime<chrono::Utc>>,
}

/// Issue comment
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IssueComment {
    pub id: i64,
    pub issue_id: i64,
    pub author_id: i64,
    pub content: String,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
}

/// Issue label
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IssueLabel {
    pub id: i64,
    pub repo_id: i64,
    pub name: String,
    pub color: String,
}

/// Milestone
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Milestone {
    pub id: i64,
    pub repo_id: i64,
    pub title: String,
    pub description: String,
    pub due_date: Option<chrono::DateTime<chrono::Utc>>,
    pub is_closed: bool,
}
