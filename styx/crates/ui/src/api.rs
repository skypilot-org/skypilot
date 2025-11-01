//! API client for Styx backend

use gloo_net::http::Request;
use serde::{Deserialize, Serialize};

const API_BASE: &str = "http://localhost:8080/api/v1";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Task {
    pub id: String,
    pub name: String,
    pub status: String,
    pub created_at: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct SubmitTaskRequest {
    pub name: String,
    pub command: String,
    pub args: Option<Vec<String>>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct SubmitTaskResponse {
    pub task_id: String,
    pub status: String,
}

/// Submit a new task
pub async fn submit_task(request: SubmitTaskRequest) -> Result<SubmitTaskResponse, String> {
    let response = Request::post(&format!("{}/tasks", API_BASE))
        .json(&request)
        .map_err(|e| format!("Failed to serialize request: {}", e))?
        .send()
        .await
        .map_err(|e| format!("Request failed: {}", e))?;

    if !response.ok() {
        return Err(format!("Server error: {}", response.status()));
    }

    response
        .json()
        .await
        .map_err(|e| format!("Failed to parse response: {}", e))
}

/// Get all tasks
pub async fn get_tasks() -> Result<Vec<Task>, String> {
    let response = Request::get(&format!("{}/tasks", API_BASE))
        .send()
        .await
        .map_err(|e| format!("Request failed: {}", e))?;

    if !response.ok() {
        return Err(format!("Server error: {}", response.status()));
    }

    response
        .json()
        .await
        .map_err(|e| format!("Failed to parse response: {}", e))
}

/// Health check
pub async fn health_check() -> Result<bool, String> {
    let response = Request::get("http://localhost:8080/health")
        .send()
        .await
        .map_err(|e| format!("Request failed: {}", e))?;

    Ok(response.ok())
}
