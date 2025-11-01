//! Pull request management

use serde::{Deserialize, Serialize};

/// Pull request model
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PullRequest {
    pub id: i64,
    pub repo_id: i64,
    pub index: i64,
    pub title: String,
    pub content: String,
    pub head_branch: String,
    pub base_branch: String,
    pub author_id: i64,
    pub assignee_id: Option<i64>,
    pub is_merged: bool,
    pub is_closed: bool,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
    pub merged_at: Option<chrono::DateTime<chrono::Utc>>,
    pub closed_at: Option<chrono::DateTime<chrono::Utc>>,
}

/// Pull request comment
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PullRequestComment {
    pub id: i64,
    pub pr_id: i64,
    pub author_id: i64,
    pub content: String,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
}

/// Pull request status
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum PullRequestStatus {
    Open,
    Merged,
    Closed,
}
