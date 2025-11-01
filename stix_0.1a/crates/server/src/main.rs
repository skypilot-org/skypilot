//! Styx API Server

use axum::{
    extract::{Path, State},
    http::StatusCode,
    routing::{get, post},
    Json, Router,
};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::net::SocketAddr;
use std::sync::Arc;
use tokio::sync::RwLock;
use tower_http::cors::CorsLayer;
use tracing::{info, Level};
use tracing_subscriber;

#[derive(Clone)]
struct AppState {
    tasks: Arc<RwLock<Vec<StoredTask>>>,
}

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

#[derive(Debug, Clone, Serialize)]
struct StoredTask {
    id: String,
    name: String,
    command: String,
    args: Vec<String>,
    status: String,
    stdout: Option<String>,
    stderr: Option<String>,
    submitted_at: DateTime<Utc>,
    updated_at: DateTime<Utc>,
}

#[derive(Deserialize)]
struct UpdateTaskRequest {
    status: String,
    stdout: Option<String>,
    stderr: Option<String>,
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
    State(state): State<AppState>,
    Json(payload): Json<SubmitTaskRequest>,
) -> Result<Json<SubmitTaskResponse>, StatusCode> {
    info!("Submitting task: {}", payload.name);

    let task_id = uuid::Uuid::new_v4().to_string();
    let task = StoredTask {
        id: task_id.clone(),
        name: payload.name.clone(),
        command: payload.command.clone(),
        args: payload.args.unwrap_or_default(),
        status: "queued".to_string(),
        stdout: None,
        stderr: None,
        submitted_at: Utc::now(),
        updated_at: Utc::now(),
    };

    state.tasks.write().await.push(task);

    Ok(Json(SubmitTaskResponse {
        task_id,
        status: "submitted".to_string(),
    }))
}

async fn list_tasks(State(state): State<AppState>) -> Result<Json<Vec<StoredTask>>, StatusCode> {
    let tasks = state.tasks.read().await.clone();
    Ok(Json(tasks))
}

async fn update_task(
    State(state): State<AppState>,
    Path(task_id): Path<String>,
    Json(payload): Json<UpdateTaskRequest>,
) -> Result<StatusCode, StatusCode> {
    let mut tasks = state.tasks.write().await;
    if let Some(task) = tasks.iter_mut().find(|task| task.id == task_id) {
        task.status = payload.status;
        task.stdout = payload.stdout;
        task.stderr = payload.stderr;
        task.updated_at = Utc::now();
        Ok(StatusCode::OK)
    } else {
        Err(StatusCode::NOT_FOUND)
    }
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Setup logging
    tracing_subscriber::fmt()
        .with_max_level(Level::INFO)
        .with_target(false)
        .init();

    // Build router
    let state = AppState {
        tasks: Arc::new(RwLock::new(Vec::new())),
    };

    let app = Router::new()
        .route("/health", get(health))
        .route("/version", get(version))
        .route("/api/v1/tasks", post(submit_task))
        .route("/api/v1/tasks", get(list_tasks))
        .route("/api/v1/tasks/:id", post(update_task))
        .with_state(state)
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
