//! API Models - Complete request/response types

use serde::{Deserialize, Serialize};
use crate::cave::{CaveConfig, CaveType};

// ========== Sandbox Models ==========

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

/// Create sandbox request
#[derive(Debug, Deserialize)]
pub struct CreateSandboxRequest {
    pub name: String,
    pub cave_type: CaveType,
    pub config: CaveConfig,
}

/// Create sandbox response
#[derive(Debug, Serialize)]
pub struct CreateSandboxResponse {
    pub cave_id: String,
    pub name: String,
    pub status: String,
}

// ========== Shell Models ==========

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

// ========== File Models ==========

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

/// File list request
#[derive(Debug, Deserialize)]
pub struct FileListRequest {
    pub path: String,
}

/// File list response
#[derive(Debug, Serialize)]
pub struct FileListResponse {
    pub files: Vec<FileInfo>,
}

/// File info
#[derive(Debug, Serialize)]
pub struct FileInfo {
    pub name: String,
    pub size: String,
    pub is_directory: bool,
    pub permissions: String,
    pub modified: String,
}

/// File upload request
#[derive(Debug, Deserialize)]
pub struct FileUploadRequest {
    pub path: String,
    pub content_base64: String,
}

/// File upload response
#[derive(Debug, Serialize)]
pub struct FileUploadResponse {
    pub success: bool,
    pub path: String,
    pub size: u64,
}

/// File download request
#[derive(Debug, Deserialize)]
pub struct FileDownloadRequest {
    pub path: String,
}

/// File download response
#[derive(Debug, Serialize)]
pub struct FileDownloadResponse {
    pub content_base64: String,
    pub filename: String,
    pub size: u64,
}

// ========== Browser Models ==========

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

/// Browser navigate request
#[derive(Debug, Deserialize)]
pub struct BrowserNavigateRequest {
    pub url: String,
    pub wait_until: Option<String>, // load, domcontentloaded, networkidle
}

/// Browser navigate response
#[derive(Debug, Serialize)]
pub struct BrowserNavigateResponse {
    pub success: bool,
    pub url: String,
    pub title: String,
}

// ========== Jupyter Models ==========

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

// ========== Error Models ==========

/// Error response
#[derive(Debug, Serialize)]
pub struct ErrorResponse {
    pub error: String,
    pub message: String,
    pub code: u16,
}
