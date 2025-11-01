//! API request/response models

use serde::{Deserialize, Serialize};

#[derive(Debug, Deserialize)]
pub struct CreateRepoRequest {
    pub owner_id: i64,
    pub name: String,
    pub description: Option<String>,
    pub is_private: Option<bool>,
}

#[derive(Debug, Deserialize)]
pub struct CreateIssueRequest {
    pub author_id: i64,
    pub title: String,
    pub content: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct CreatePullRequest {
    pub author_id: i64,
    pub title: String,
    pub content: Option<String>,
    pub head_branch: String,
    pub base_branch: String,
}
