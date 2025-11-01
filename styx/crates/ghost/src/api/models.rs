//! API Models

use serde::{Deserialize, Serialize};

/// Sandbox info response
#[derive(Debug, Serialize, Deserialize)]
pub struct SandboxInfo {
    pub cave_id: String,
    pub status: String,
    pub uptime: u64,
    pub resources: ResourceUsage,
}

/// Resource usage
#[derive(Debug, Serialize, Deserialize)]
pub struct ResourceUsage {
    pub cpu_percent: f64,
    pub memory_mb: u64,
    pub disk_mb: u64,
}

/// Shell execution request
#[derive(Debug, Deserialize)]
pub struct ShellExecRequest {
    pub command: String,
    pub timeout: Option<u64>,
    pub cwd: Option<String>,
}

/// Shell execution response
#[derive(Debug, Serialize)]
pub struct ShellExecResponse {
    pub stdout: String,
    pub stderr: String,
    pub exit_code: i32,
    pub duration_ms: u64,
}

/// File read request
#[derive(Debug, Deserialize)]
pub struct FileReadRequest {
    pub path: String,
}

/// File read response
#[derive(Debug, Serialize)]
pub struct FileReadResponse {
    pub content: String,
    pub size: u64,
    pub mime_type: String,
}

/// File write request
#[derive(Debug, Deserialize)]
pub struct FileWriteRequest {
    pub path: String,
    pub content: String,
    pub append: Option<bool>,
}

/// File write response
#[derive(Debug, Serialize)]
pub struct FileWriteResponse {
    pub success: bool,
    pub bytes_written: u64,
}

/// Browser screenshot request
#[derive(Debug, Deserialize)]
pub struct ScreenshotRequest {
    pub url: Option<String>,
    pub width: Option<u32>,
    pub height: Option<u32>,
    pub format: Option<String>, // png, jpeg
}

/// Browser screenshot response
#[derive(Debug, Serialize)]
pub struct ScreenshotResponse {
    pub image_base64: String,
    pub width: u32,
    pub height: u32,
    pub format: String,
}

/// Jupyter execution request
#[derive(Debug, Deserialize)]
pub struct JupyterExecRequest {
    pub code: String,
    pub kernel: Option<String>, // python3, r, julia
}

/// Jupyter execution response
#[derive(Debug, Serialize)]
pub struct JupyterExecResponse {
    pub output: String,
    pub error: Option<String>,
    pub execution_count: u64,
}

/// Error response
#[derive(Debug, Serialize)]
pub struct ErrorResponse {
    pub error: String,
    pub message: String,
    pub code: u16,
}
