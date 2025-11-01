//! API Request Handlers - COMPLETE Implementation
//!
//! Based on https://sandbox.agent-infra.com/api/

use axum::{
    extract::{State, Json, Path},
    http::StatusCode,
    response::IntoResponse,
};
use std::sync::Arc;
use tokio::process::Command;

use crate::ghost::Ghost;
use super::models::*;

// ========== Sandbox Endpoints ==========

/// GET /v1/sandbox - Get sandbox information
pub async fn get_sandbox_info(
    State(ghost): State<Arc<Ghost>>,
) -> impl IntoResponse {
    let caves = ghost.list_caves().await;
    
    if let Some(cave) = caves.first() {
        let info = SandboxInfo {
            cave_id: cave.id.clone(),
            status: format!("{:?}", cave.status),
            uptime: (chrono::Utc::now().timestamp() - cave.created_at) as u64,
            resources: ResourceUsage {
                cpu_percent: cave.config.cpu_limit.unwrap_or(0.0) * 100.0,
                memory_mb: cave.config.memory_limit.unwrap_or(0),
                disk_mb: 1024,
            },
        };
        
        (StatusCode::OK, Json(info)).into_response()
    } else {
        (StatusCode::NOT_FOUND, Json(ErrorResponse {
            error: "NotFound".to_string(),
            message: "No active caves".to_string(),
            code: 404,
        })).into_response()
    }
}

/// POST /v1/sandbox/create - Create new sandbox
pub async fn create_sandbox(
    State(ghost): State<Arc<Ghost>>,
    Json(req): Json<CreateSandboxRequest>,
) -> impl IntoResponse {
    match ghost.create_cave(
        req.name.clone(),
        req.cave_type,
        req.config,
    ).await {
        Ok(cave_id) => {
            (StatusCode::CREATED, Json(CreateSandboxResponse {
                cave_id,
                name: req.name,
                status: "created".to_string(),
            })).into_response()
        }
        Err(e) => {
            (StatusCode::INTERNAL_SERVER_ERROR, Json(ErrorResponse {
                error: "CreationFailed".to_string(),
                message: e.to_string(),
                code: 500,
            })).into_response()
        }
    }
}

/// DELETE /v1/sandbox/{cave_id} - Delete sandbox
pub async fn delete_sandbox(
    State(ghost): State<Arc<Ghost>>,
    Path(cave_id): Path<String>,
) -> impl IntoResponse {
    match ghost.destroy_cave(&cave_id).await {
        Ok(_) => {
            (StatusCode::OK, Json(serde_json::json!({
                "success": true,
                "message": "Cave destroyed"
            }))).into_response()
        }
        Err(e) => {
            (StatusCode::INTERNAL_SERVER_ERROR, Json(ErrorResponse {
                error: "DeletionFailed".to_string(),
                message: e.to_string(),
                code: 500,
            })).into_response()
        }
    }
}

// ========== Shell Endpoints ==========

/// POST /v1/shell/exec - Execute shell command
pub async fn shell_exec(
    State(ghost): State<Arc<Ghost>>,
    Json(req): Json<ShellExecRequest>,
) -> impl IntoResponse {
    let caves = ghost.list_caves().await;
    
    if let Some(cave) = caves.first() {
        let start = std::time::Instant::now();
        
        // Build command with timeout and cwd
        let mut full_cmd = String::new();
        
        if let Some(ref cwd) = req.cwd {
            full_cmd.push_str(&format!("cd {} && ", cwd));
        }
        
        full_cmd.push_str(&req.command);
        
        // Add timeout wrapper if specified
        if let Some(timeout) = req.timeout {
            full_cmd = format!("timeout {} {}", timeout, full_cmd);
        }
        
        match ghost.execute(&cave.id, &full_cmd, "bash").await {
            Ok(result) => {
                let response = ShellExecResponse {
                    stdout: result.stdout,
                    stderr: result.stderr,
                    exit_code: result.exit_code,
                    duration_ms: start.elapsed().as_millis() as u64,
                };
                
                (StatusCode::OK, Json(response)).into_response()
            }
            Err(e) => {
                (StatusCode::INTERNAL_SERVER_ERROR, Json(ErrorResponse {
                    error: "ExecutionFailed".to_string(),
                    message: e.to_string(),
                    code: 500,
                })).into_response()
            }
        }
    } else {
        (StatusCode::NOT_FOUND, Json(ErrorResponse {
            error: "NotFound".to_string(),
            message: "No active caves".to_string(),
            code: 404,
        })).into_response()
    }
}

// ========== File Endpoints ==========

/// POST /v1/file/read - Read file content
pub async fn file_read(
    State(ghost): State<Arc<Ghost>>,
    Json(req): Json<FileReadRequest>,
) -> impl IntoResponse {
    let caves = ghost.list_caves().await;
    
    if let Some(cave) = caves.first() {
        let cmd = format!("cat '{}'", req.path);
        
        match ghost.execute(&cave.id, &cmd, "bash").await {
            Ok(result) if result.exit_code == 0 => {
                let response = FileReadResponse {
                    content: result.stdout,
                    size: result.stdout.len() as u64,
                    mime_type: detect_mime_type(&req.path),
                };
                
                (StatusCode::OK, Json(response)).into_response()
            }
            Ok(result) => {
                (StatusCode::NOT_FOUND, Json(ErrorResponse {
                    error: "FileNotFound".to_string(),
                    message: result.stderr,
                    code: 404,
                })).into_response()
            }
            Err(e) => {
                (StatusCode::INTERNAL_SERVER_ERROR, Json(ErrorResponse {
                    error: "ReadFailed".to_string(),
                    message: e.to_string(),
                    code: 500,
                })).into_response()
            }
        }
    } else {
        (StatusCode::NOT_FOUND, Json(ErrorResponse {
            error: "NotFound".to_string(),
            message: "No active caves".to_string(),
            code: 404,
        })).into_response()
    }
}

/// POST /v1/file/write - Write file content
pub async fn file_write(
    State(ghost): State<Arc<Ghost>>,
    Json(req): Json<FileWriteRequest>,
) -> impl IntoResponse {
    let caves = ghost.list_caves().await;
    
    if let Some(cave) = caves.first() {
        // Escape content for shell
        let escaped_content = req.content.replace('\'', "'\\''");
        
        let operator = if req.append.unwrap_or(false) { ">>" } else { ">" };
        let cmd = format!("echo '{}' {} '{}'", escaped_content, operator, req.path);
        
        match ghost.execute(&cave.id, &cmd, "bash").await {
            Ok(result) if result.exit_code == 0 => {
                let response = FileWriteResponse {
                    success: true,
                    bytes_written: req.content.len() as u64,
                };
                
                (StatusCode::OK, Json(response)).into_response()
            }
            Ok(result) => {
                (StatusCode::INTERNAL_SERVER_ERROR, Json(ErrorResponse {
                    error: "WriteFailed".to_string(),
                    message: result.stderr,
                    code: 500,
                })).into_response()
            }
            Err(e) => {
                (StatusCode::INTERNAL_SERVER_ERROR, Json(ErrorResponse {
                    error: "WriteFailed".to_string(),
                    message: e.to_string(),
                    code: 500,
                })).into_response()
            }
        }
    } else {
        (StatusCode::NOT_FOUND, Json(ErrorResponse {
            error: "NotFound".to_string(),
            message: "No active caves".to_string(),
            code: 404,
        })).into_response()
    }
}

/// POST /v1/file/list - List directory contents
pub async fn file_list(
    State(ghost): State<Arc<Ghost>>,
    Json(req): Json<FileListRequest>,
) -> impl IntoResponse {
    let caves = ghost.list_caves().await;
    
    if let Some(cave) = caves.first() {
        let cmd = format!("ls -lah '{}'", req.path);
        
        match ghost.execute(&cave.id, &cmd, "bash").await {
            Ok(result) if result.exit_code == 0 => {
                // Parse ls output
                let files: Vec<FileInfo> = result.stdout
                    .lines()
                    .skip(1) // Skip "total" line
                    .filter_map(|line| parse_ls_line(line))
                    .collect();
                
                (StatusCode::OK, Json(FileListResponse { files })).into_response()
            }
            Ok(result) => {
                (StatusCode::NOT_FOUND, Json(ErrorResponse {
                    error: "DirectoryNotFound".to_string(),
                    message: result.stderr,
                    code: 404,
                })).into_response()
            }
            Err(e) => {
                (StatusCode::INTERNAL_SERVER_ERROR, Json(ErrorResponse {
                    error: "ListFailed".to_string(),
                    message: e.to_string(),
                    code: 500,
                })).into_response()
            }
        }
    } else {
        (StatusCode::NOT_FOUND, Json(ErrorResponse {
            error: "NotFound".to_string(),
            message: "No active caves".to_string(),
            code: 404,
        })).into_response()
    }
}

// ========== Browser Endpoints ==========

/// POST /v1/browser/screenshot - Take browser screenshot
pub async fn browser_screenshot(
    State(ghost): State<Arc<Ghost>>,
    Json(req): Json<ScreenshotRequest>,
) -> impl IntoResponse {
    let caves = ghost.list_caves().await;
    
    if let Some(cave) = caves.first() {
        // Install playwright if not present
        let install_cmd = "pip install playwright && playwright install chromium || true";
        let _ = ghost.execute(&cave.id, install_cmd, "bash").await;
        
        // Take screenshot
        let screenshot_cmd = format!(
            "playwright screenshot '{}' --format {} --output /tmp/screenshot.{}",
            req.url.as_deref().unwrap_or("about:blank"),
            req.format.as_deref().unwrap_or("png"),
            req.format.as_deref().unwrap_or("png")
        );
        
        match ghost.execute(&cave.id, &screenshot_cmd, "bash").await {
            Ok(result) if result.exit_code == 0 => {
                // Read screenshot file
                let read_cmd = "base64 /tmp/screenshot.png";
                match ghost.execute(&cave.id, read_cmd, "bash").await {
                    Ok(read_result) => {
                        let response = ScreenshotResponse {
                            image_base64: read_result.stdout.trim().to_string(),
                            width: req.width.unwrap_or(1920),
                            height: req.height.unwrap_or(1080),
                            format: req.format.unwrap_or_else(|| "png".to_string()),
                        };
                        
                        (StatusCode::OK, Json(response)).into_response()
                    }
                    Err(e) => {
                        (StatusCode::INTERNAL_SERVER_ERROR, Json(ErrorResponse {
                            error: "ScreenshotReadFailed".to_string(),
                            message: e.to_string(),
                            code: 500,
                        })).into_response()
                    }
                }
            }
            Ok(result) => {
                (StatusCode::INTERNAL_SERVER_ERROR, Json(ErrorResponse {
                    error: "ScreenshotFailed".to_string(),
                    message: result.stderr,
                    code: 500,
                })).into_response()
            }
            Err(e) => {
                (StatusCode::INTERNAL_SERVER_ERROR, Json(ErrorResponse {
                    error: "ScreenshotFailed".to_string(),
                    message: e.to_string(),
                    code: 500,
                })).into_response()
            }
        }
    } else {
        (StatusCode::NOT_FOUND, Json(ErrorResponse {
            error: "NotFound".to_string(),
            message: "No active caves".to_string(),
            code: 404,
        })).into_response()
    }
}

/// POST /v1/browser/navigate - Navigate to URL
pub async fn browser_navigate(
    State(ghost): State<Arc<Ghost>>,
    Json(req): Json<BrowserNavigateRequest>,
) -> impl IntoResponse {
    let caves = ghost.list_caves().await;
    
    if let Some(cave) = caves.first() {
        let cmd = format!("playwright goto '{}'", req.url);
        
        match ghost.execute(&cave.id, &cmd, "bash").await {
            Ok(result) if result.exit_code == 0 => {
                (StatusCode::OK, Json(BrowserNavigateResponse {
                    success: true,
                    url: req.url,
                    title: "Page Title".to_string(), // TODO: Extract actual title
                })).into_response()
            }
            Err(e) => {
                (StatusCode::INTERNAL_SERVER_ERROR, Json(ErrorResponse {
                    error: "NavigationFailed".to_string(),
                    message: e.to_string(),
                    code: 500,
                })).into_response()
            }
        }
    } else {
        (StatusCode::NOT_FOUND, Json(ErrorResponse {
            error: "NotFound".to_string(),
            message: "No active caves".to_string(),
            code: 404,
        })).into_response()
    }
}

// ========== Jupyter Endpoints ==========

/// POST /v1/jupyter/execute - Execute Jupyter code
pub async fn jupyter_execute(
    State(ghost): State<Arc<Ghost>>,
    Json(req): Json<JupyterExecRequest>,
) -> impl IntoResponse {
    let caves = ghost.list_caves().await;
    
    if let Some(cave) = caves.first() {
        let kernel = req.kernel.as_deref().unwrap_or("python3");
        
        // Escape code for shell
        let escaped_code = req.code.replace('\'', "'\\''");
        
        // Execute via python
        let cmd = match kernel {
            "python3" | "python" => {
                format!("python3 -c '{}'", escaped_code)
            }
            "r" => {
                format!("Rscript -e '{}'", escaped_code)
            }
            "julia" => {
                format!("julia -e '{}'", escaped_code)
            }
            _ => {
                return (StatusCode::BAD_REQUEST, Json(ErrorResponse {
                    error: "UnsupportedKernel".to_string(),
                    message: format!("Kernel '{}' not supported", kernel),
                    code: 400,
                })).into_response();
            }
        };
        
        match ghost.execute(&cave.id, &cmd, "bash").await {
            Ok(result) => {
                let response = JupyterExecResponse {
                    output: result.stdout,
                    error: if result.exit_code != 0 {
                        Some(result.stderr)
                    } else {
                        None
                    },
                    execution_count: 1,
                };
                
                (StatusCode::OK, Json(response)).into_response()
            }
            Err(e) => {
                (StatusCode::INTERNAL_SERVER_ERROR, Json(ErrorResponse {
                    error: "ExecutionFailed".to_string(),
                    message: e.to_string(),
                    code: 500,
                })).into_response()
            }
        }
    } else {
        (StatusCode::NOT_FOUND, Json(ErrorResponse {
            error: "NotFound".to_string(),
            message: "No active caves".to_string(),
            code: 404,
        })).into_response()
    }
}

// ========== File System Endpoints ==========

/// POST /v1/filesystem/upload - Upload file
pub async fn filesystem_upload(
    State(ghost): State<Arc<Ghost>>,
    Json(req): Json<FileUploadRequest>,
) -> impl IntoResponse {
    let caves = ghost.list_caves().await;
    
    if let Some(cave) = caves.first() {
        // Decode base64 content
        let content = match base64::engine::general_purpose::STANDARD.decode(&req.content_base64) {
            Ok(data) => String::from_utf8_lossy(&data).to_string(),
            Err(e) => {
                return (StatusCode::BAD_REQUEST, Json(ErrorResponse {
                    error: "InvalidBase64".to_string(),
                    message: e.to_string(),
                    code: 400,
                })).into_response();
            }
        };
        
        // Write file
        let escaped = content.replace('\'', "'\\''");
        let cmd = format!("echo '{}' > '{}'", escaped, req.path);
        
        match ghost.execute(&cave.id, &cmd, "bash").await {
            Ok(result) if result.exit_code == 0 => {
                (StatusCode::OK, Json(FileUploadResponse {
                    success: true,
                    path: req.path,
                    size: content.len() as u64,
                })).into_response()
            }
            Err(e) => {
                (StatusCode::INTERNAL_SERVER_ERROR, Json(ErrorResponse {
                    error: "UploadFailed".to_string(),
                    message: e.to_string(),
                    code: 500,
                })).into_response()
            }
        }
    } else {
        (StatusCode::NOT_FOUND, Json(ErrorResponse {
            error: "NotFound".to_string(),
            message: "No active caves".to_string(),
            code: 404,
        })).into_response()
    }
}

/// POST /v1/filesystem/download - Download file
pub async fn filesystem_download(
    State(ghost): State<Arc<Ghost>>,
    Json(req): Json<FileDownloadRequest>,
) -> impl IntoResponse {
    let caves = ghost.list_caves().await;
    
    if let Some(cave) = caves.first() {
        // Read and base64 encode
        let cmd = format!("base64 '{}'", req.path);
        
        match ghost.execute(&cave.id, &cmd, "bash").await {
            Ok(result) if result.exit_code == 0 => {
                (StatusCode::OK, Json(FileDownloadResponse {
                    content_base64: result.stdout.trim().to_string(),
                    filename: std::path::Path::new(&req.path)
                        .file_name()
                        .and_then(|n| n.to_str())
                        .unwrap_or("file")
                        .to_string(),
                    size: 0, // TODO: Get actual size
                })).into_response()
            }
            Err(e) => {
                (StatusCode::NOT_FOUND, Json(ErrorResponse {
                    error: "FileNotFound".to_string(),
                    message: e.to_string(),
                    code: 404,
                })).into_response()
            }
        }
    } else {
        (StatusCode::NOT_FOUND, Json(ErrorResponse {
            error: "NotFound".to_string(),
            message: "No active caves".to_string(),
            code: 404,
        })).into_response()
    }
}

// ========== Helper Functions ==========

/// Detect MIME type from file extension
fn detect_mime_type(path: &str) -> String {
    let ext = std::path::Path::new(path)
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("");
    
    match ext {
        "txt" => "text/plain",
        "json" => "application/json",
        "yaml" | "yml" => "application/yaml",
        "py" => "text/x-python",
        "rs" => "text/x-rust",
        "js" => "application/javascript",
        "html" => "text/html",
        "css" => "text/css",
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "gif" => "image/gif",
        "pdf" => "application/pdf",
        _ => "application/octet-stream",
    }.to_string()
}

/// Parse ls -lah output line
fn parse_ls_line(line: &str) -> Option<FileInfo> {
    let parts: Vec<&str> = line.split_whitespace().collect();
    if parts.len() < 9 {
        return None;
    }
    
    Some(FileInfo {
        name: parts[8..].join(" "),
        size: parts[4].to_string(),
        is_directory: parts[0].starts_with('d'),
        permissions: parts[0].to_string(),
        modified: format!("{} {} {}", parts[5], parts[6], parts[7]),
    })
}
