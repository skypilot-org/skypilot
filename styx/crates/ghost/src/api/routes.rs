//! API Routes

use axum::{
    routing::{get, post},
    Router,
};
use std::sync::Arc;
use tower_http::cors::CorsLayer;

use crate::ghost::Ghost;
use super::handlers;

/// Create API router
pub fn create_router(ghost: Arc<Ghost>) -> Router {
    Router::new()
        // Core API endpoints
        .route("/v1/sandbox", get(handlers::get_sandbox_info))
        .route("/v1/shell/exec", post(handlers::shell_exec))
        .route("/v1/file/read", post(handlers::file_read))
        .route("/v1/file/write", post(handlers::file_write))
        .route("/v1/browser/screenshot", post(handlers::browser_screenshot))
        .route("/v1/jupyter/execute", post(handlers::jupyter_execute))
        
        // Health check
        .route("/health", get(|| async { "OK" }))
        
        // Add CORS
        .layer(CorsLayer::permissive())
        
        // Add state
        .with_state(ghost)
}
