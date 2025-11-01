//! API Routes - Complete endpoint definitions
//!
//! Based on https://sandbox.agent-infra.com/api/

use axum::{
    routing::{get, post, delete},
    Router,
};
use std::sync::Arc;
use tower_http::cors::CorsLayer;

use crate::ghost::Ghost;
use super::handlers;

/// Create complete API router
pub fn create_router(ghost: Arc<Ghost>) -> Router {
    Router::new()
        // ========== Sandbox Endpoints ==========
        .route("/v1/sandbox", get(handlers::get_sandbox_info))
        .route("/v1/sandbox", post(handlers::create_sandbox))
        .route("/v1/sandbox/:cave_id", delete(handlers::delete_sandbox))
        
        // ========== Shell Endpoints ==========
        .route("/v1/shell/exec", post(handlers::shell_exec))
        
        // ========== File Endpoints ==========
        .route("/v1/file/read", post(handlers::file_read))
        .route("/v1/file/write", post(handlers::file_write))
        .route("/v1/file/list", post(handlers::file_list))
        
        // ========== File System Endpoints ==========
        .route("/v1/filesystem/upload", post(handlers::filesystem_upload))
        .route("/v1/filesystem/download", post(handlers::filesystem_download))
        
        // ========== Browser Endpoints ==========
        .route("/v1/browser/screenshot", post(handlers::browser_screenshot))
        .route("/v1/browser/navigate", post(handlers::browser_navigate))
        
        // ========== Jupyter Endpoints ==========
        .route("/v1/jupyter/execute", post(handlers::jupyter_execute))
        
        // ========== Health & Info ==========
        .route("/health", get(|| async { "OK" }))
        .route("/version", get(|| async { 
            serde_json::json!({
                "version": env!("CARGO_PKG_VERSION"),
                "name": "Ghost",
                "description": "Isolated execution caves"
            }).to_string()
        }))
        
        // Add CORS middleware
        .layer(CorsLayer::permissive())
        
        // Add state
        .with_state(ghost)
}

/// Create MCP router
pub fn create_mcp_router(ghost: Arc<Ghost>) -> Router {
    Router::new()
        .route("/mcp/tools", get(mcp_list_tools))
        .route("/mcp/execute", post(mcp_execute_tool))
        .with_state(ghost)
}

/// List MCP tools
async fn mcp_list_tools(
    State(ghost): State<Arc<Ghost>>,
) -> impl IntoResponse {
    use crate::mcp::McpHub;
    
    let caves = ghost.list_caves().await;
    if let Some(cave) = caves.first() {
        let hub = McpHub::new(&cave.id);
        let tools = hub.list_tools();
        
        (axum::http::StatusCode::OK, axum::Json(tools)).into_response()
    } else {
        (axum::http::StatusCode::NOT_FOUND, "No active caves").into_response()
    }
}

/// Execute MCP tool
async fn mcp_execute_tool(
    State(ghost): State<Arc<Ghost>>,
    axum::Json(req): axum::Json<serde_json::Value>,
) -> impl IntoResponse {
    use crate::mcp::McpHub;
    
    let caves = ghost.list_caves().await;
    if let Some(cave) = caves.first() {
        let hub = McpHub::new(&cave.id);
        
        let tool_name = req.get("tool")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        
        let params = req.get("params")
            .cloned()
            .unwrap_or(serde_json::json!({}));
        
        match hub.execute_tool(tool_name, params).await {
            Ok(result) => {
                (axum::http::StatusCode::OK, axum::Json(result)).into_response()
            }
            Err(e) => {
                (axum::http::StatusCode::INTERNAL_SERVER_ERROR, e.to_string()).into_response()
            }
        }
    } else {
        (axum::http::StatusCode::NOT_FOUND, "No active caves").into_response()
    }
}
