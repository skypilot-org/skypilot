//! Styx API Server

use axum::{
    routing::{get, post},
    Router,
    Json,
    http::StatusCode,
};
use serde::{Deserialize, Serialize};
use std::net::SocketAddr;
use tower_http::cors::CorsLayer;
use tracing::{info, Level};
use tracing_subscriber;

#[derive(Serialize)]
struct HealthResponse {
    status: String,
    version: String,
    service: String,
}

#[derive(Serialize)]
struct VersionResponse {
    version: String,
    service: String,
    rust_version: String,
}

#[derive(Deserialize)]
struct SubmitTaskRequest {
    name: String,
    command: String,
    args: Option<Vec<String>>,
}

#[derive(Serialize)]
struct SubmitTaskResponse {
    task_id: String,
    status: String,
}

async fn health() -> Json<HealthResponse> {
    Json(HealthResponse {
        status: "healthy".to_string(),
        version: styx_server::VERSION.to_string(),
        service: "styx-server".to_string(),
    })
}

async fn version() -> Json<VersionResponse> {
    Json(VersionResponse {
        version: styx_server::VERSION.to_string(),
        service: "styx".to_string(),
        rust_version: env!("CARGO_PKG_RUST_VERSION").to_string(),
    })
}

async fn submit_task(
    Json(payload): Json<SubmitTaskRequest>,
) -> Result<Json<SubmitTaskResponse>, StatusCode> {
    info!("Submitting task: {}", payload.name);

    // TODO: Actually submit to scheduler
    let task_id = uuid::Uuid::new_v4().to_string();

    Ok(Json(SubmitTaskResponse {
        task_id,
        status: "submitted".to_string(),
    }))
}

async fn list_tasks() -> Result<Json<Vec<String>>, StatusCode> {
    // TODO: Get from scheduler
    Ok(Json(vec![]))
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Setup logging
    tracing_subscriber::fmt()
        .with_max_level(Level::INFO)
        .with_target(false)
        .init();

    // Build router
    let app = Router::new()
        .route("/health", get(health))
        .route("/version", get(version))
        .route("/api/v1/tasks", post(submit_task))
        .route("/api/v1/tasks", get(list_tasks))
        .layer(CorsLayer::permissive());

    // Start server
    let addr = SocketAddr::from(([0, 0, 0, 0], 8080));
    
    info!("?? Styx Server starting on http://{}", addr);
    info!("Health check: http://{}/health", addr);
    info!("API: http://{}/api/v1/tasks", addr);

    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, app).await?;

    Ok(())
}
