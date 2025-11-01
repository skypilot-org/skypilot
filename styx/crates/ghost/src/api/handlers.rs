//! API Request Handlers

use axum::{
    extract::{State, Json},
    http::StatusCode,
    response::IntoResponse,
};
use std::sync::Arc;

use crate::ghost::Ghost;
use super::models::*;

/// Get sandbox info
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
                disk_mb: 1024, // TODO: Get actual disk usage
            },
        };
        
        Json(info).into_response()
    } else {
        (StatusCode::NOT_FOUND, "No active caves").into_response()
    }
}

/// Execute shell command
pub async fn shell_exec(
    State(ghost): State<Arc<Ghost>>,
    Json(req): Json<ShellExecRequest>,
) -> impl IntoResponse {
    let caves = ghost.list_caves().await;
    
    if let Some(cave) = caves.first() {
        let start = std::time::Instant::now();
        
        match ghost.execute(&cave.id, &req.command, "bash").await {
            Ok(result) => {
                let response = ShellExecResponse {
                    stdout: result.stdout,
                    stderr: result.stderr,
                    exit_code: result.exit_code,
                    duration_ms: start.elapsed().as_millis() as u64,
                };
                
                Json(response).into_response()
            }
            Err(e) => {
                (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()).into_response()
            }
        }
    } else {
        (StatusCode::NOT_FOUND, "No active caves").into_response()
    }
}

/// Read file
pub async fn file_read(
    State(ghost): State<Arc<Ghost>>,
    Json(req): Json<FileReadRequest>,
) -> impl IntoResponse {
    let caves = ghost.list_caves().await;
    
    if let Some(cave) = caves.first() {
        let cmd = format!("cat {}", req.path);
        
        match ghost.execute(&cave.id, &cmd, "bash").await {
            Ok(result) if result.exit_code == 0 => {
                let response = FileReadResponse {
                    content: result.stdout,
                    size: result.stdout.len() as u64,
                    mime_type: "text/plain".to_string(), // TODO: Detect mime type
                };
                
                Json(response).into_response()
            }
            Ok(result) => {
                (StatusCode::NOT_FOUND, result.stderr).into_response()
            }
            Err(e) => {
                (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()).into_response()
            }
        }
    } else {
        (StatusCode::NOT_FOUND, "No active caves").into_response()
    }
}

/// Write file
pub async fn file_write(
    State(ghost): State<Arc<Ghost>>,
    Json(req): Json<FileWriteRequest>,
) -> impl IntoResponse {
    let caves = ghost.list_caves().await;
    
    if let Some(cave) = caves.first() {
        let operator = if req.append.unwrap_or(false) { ">>" } else { ">" };
        let cmd = format!("echo '{}' {} {}", req.content, operator, req.path);
        
        match ghost.execute(&cave.id, &cmd, "bash").await {
            Ok(result) if result.exit_code == 0 => {
                let response = FileWriteResponse {
                    success: true,
                    bytes_written: req.content.len() as u64,
                };
                
                Json(response).into_response()
            }
            Ok(result) => {
                (StatusCode::INTERNAL_SERVER_ERROR, result.stderr).into_response()
            }
            Err(e) => {
                (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()).into_response()
            }
        }
    } else {
        (StatusCode::NOT_FOUND, "No active caves").into_response()
    }
}

/// Take browser screenshot
pub async fn browser_screenshot(
    State(ghost): State<Arc<Ghost>>,
    Json(req): Json<ScreenshotRequest>,
) -> impl IntoResponse {
    let caves = ghost.list_caves().await;
    
    if let Some(cave) = caves.first() {
        // Use playwright or puppeteer to take screenshot
        let cmd = format!(
            "playwright screenshot {} --format {}",
            req.url.as_deref().unwrap_or("about:blank"),
            req.format.as_deref().unwrap_or("png")
        );
        
        match ghost.execute(&cave.id, &cmd, "bash").await {
            Ok(result) if result.exit_code == 0 => {
                let response = ScreenshotResponse {
                    image_base64: base64::engine::general_purpose::STANDARD.encode(&result.stdout),
                    width: req.width.unwrap_or(1920),
                    height: req.height.unwrap_or(1080),
                    format: req.format.unwrap_or_else(|| "png".to_string()),
                };
                
                Json(response).into_response()
            }
            Ok(result) => {
                (StatusCode::INTERNAL_SERVER_ERROR, result.stderr).into_response()
            }
            Err(e) => {
                (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()).into_response()
            }
        }
    } else {
        (StatusCode::NOT_FOUND, "No active caves").into_response()
    }
}

/// Execute Jupyter code
pub async fn jupyter_execute(
    State(ghost): State<Arc<Ghost>>,
    Json(req): Json<JupyterExecRequest>,
) -> impl IntoResponse {
    let caves = ghost.list_caves().await;
    
    if let Some(cave) = caves.first() {
        // Execute via jupyter console or ipython
        let cmd = format!("python3 -c '{}'", req.code);
        
        match ghost.execute(&cave.id, &cmd, "python").await {
            Ok(result) => {
                let response = JupyterExecResponse {
                    output: result.stdout,
                    error: if result.exit_code != 0 {
                        Some(result.stderr)
                    } else {
                        None
                    },
                    execution_count: 1, // TODO: Track execution count
                };
                
                Json(response).into_response()
            }
            Err(e) => {
                (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()).into_response()
            }
        }
    } else {
        (StatusCode::NOT_FOUND, "No active caves").into_response()
    }
}
