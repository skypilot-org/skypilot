//! API client for backend

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize)]
pub struct SubmitTaskRequest {
    pub name: String,
    pub command: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct SubmitTaskResponse {
    pub task_id: String,
    pub status: String,
}

/// Submit task to backend
pub async fn submit_task(request: SubmitTaskRequest) -> Result<SubmitTaskResponse, String> {
    // TODO: Implement HTTP request
    Err("Not implemented yet".to_string())
}
