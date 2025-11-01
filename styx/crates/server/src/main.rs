//! Styx API Server - REAL IMPLEMENTATION WITH SQLITE!

use axum::{
    routing::{get, post},
    Router,
    Json,
    http::StatusCode,
    extract::{State, Path},
};
use serde::{Deserialize, Serialize};
use std::net::SocketAddr;
use std::sync::Arc;
use tower_http::cors::CorsLayer;
use tracing::{info, error, Level};
use tracing_subscriber;
use sqlx::{SqlitePool, sqlite::SqlitePoolOptions};
use chrono::Utc;

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

#[derive(Clone)]
struct AppState {
    db: SqlitePool,
}

#[derive(Debug, Serialize, Deserialize, sqlx::FromRow)]
struct Task {
    task_id: String,
    name: String,
    command: String,
    args: Option<String>, // JSON string
    status: String, // pending, running, completed, failed
    output: Option<String>,
    error: Option<String>,
    created_at: String,
    updated_at: String,
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

#[derive(Deserialize)]
struct TaskResultRequest {
    status: String,
    output: Option<String>,
    error: Option<String>,
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
    State(state): State<Arc<AppState>>,
    Json(payload): Json<SubmitTaskRequest>,
) -> Result<Json<SubmitTaskResponse>, StatusCode> {
    info!("Submitting task: {}", payload.name);

    let task_id = uuid::Uuid::new_v4().to_string();
    let args_json = payload.args.map(|a| serde_json::to_string(&a).unwrap_or_default());
    let now = Utc::now().to_rfc3339();

    // Insert into SQLite
    let result = sqlx::query(
        "INSERT INTO tasks (task_id, name, command, args, status, created_at, updated_at) 
         VALUES (?, ?, ?, ?, 'pending', ?, ?)"
    )
    .bind(&task_id)
    .bind(&payload.name)
    .bind(&payload.command)
    .bind(&args_json)
    .bind(&now)
    .bind(&now)
    .execute(&state.db)
    .await;

    match result {
        Ok(_) => {
            info!("Task {} submitted successfully", task_id);
            Ok(Json(SubmitTaskResponse {
                task_id,
                status: "pending".to_string(),
            }))
        }
        Err(e) => {
            error!("Failed to submit task: {}", e);
            Err(StatusCode::INTERNAL_SERVER_ERROR)
        }
    }
}

async fn list_tasks(
    State(state): State<Arc<AppState>>,
) -> Result<Json<Vec<Task>>, StatusCode> {
    let tasks = sqlx::query_as::<_, Task>("SELECT * FROM tasks ORDER BY created_at DESC")
        .fetch_all(&state.db)
        .await
        .map_err(|e| {
            error!("Failed to fetch tasks: {}", e);
            StatusCode::INTERNAL_SERVER_ERROR
        })?;

    Ok(Json(tasks))
}

async fn get_pending_tasks(
    State(state): State<Arc<AppState>>,
) -> Result<Json<Vec<Task>>, StatusCode> {
    let tasks = sqlx::query_as::<_, Task>(
        "SELECT * FROM tasks WHERE status = 'pending' ORDER BY created_at ASC LIMIT 10"
    )
    .fetch_all(&state.db)
    .await
    .map_err(|e| {
        error!("Failed to fetch pending tasks: {}", e);
        StatusCode::INTERNAL_SERVER_ERROR
    })?;

    Ok(Json(tasks))
}

async fn report_task_result(
    State(state): State<Arc<AppState>>,
    Path(task_id): Path<String>,
    Json(payload): Json<TaskResultRequest>,
) -> Result<StatusCode, StatusCode> {
    info!("Reporting result for task {}", task_id);

    let now = Utc::now().to_rfc3339();

    let result = sqlx::query(
        "UPDATE tasks SET status = ?, output = ?, error = ?, updated_at = ? WHERE task_id = ?"
    )
    .bind(&payload.status)
    .bind(&payload.output)
    .bind(&payload.error)
    .bind(&now)
    .bind(&task_id)
    .execute(&state.db)
    .await;

    match result {
        Ok(_) => {
            info!("Task {} updated successfully", task_id);
            Ok(StatusCode::OK)
        }
        Err(e) => {
            error!("Failed to update task: {}", e);
            Err(StatusCode::INTERNAL_SERVER_ERROR)
        }
    }
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Setup logging
    tracing_subscriber::fmt()
        .with_max_level(Level::INFO)
        .with_target(false)
        .init();

    info!("? Initializing Styx Server with SQLite...");

    // Setup SQLite database
    let db_url = std::env::var("DATABASE_URL")
        .unwrap_or_else(|_| "sqlite://styx.db".to_string());
    
    info!("? Connecting to database: {}", db_url);
    let pool = SqlitePoolOptions::new()
        .max_connections(5)
        .connect(&db_url)
        .await?;

    // Run migrations
    info!("? Running database migrations...");
    sqlx::query(
        r#"
        CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            command TEXT NOT NULL,
            args TEXT,
            status TEXT NOT NULL,
            output TEXT,
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        "#
    )
    .execute(&pool)
    .await?;

    info!("?? Database ready!");

    // Create app state
    let state = Arc::new(AppState { db: pool });

    // Build router
    let app = Router::new()
        .route("/health", get(health))
        .route("/version", get(version))
        .route("/api/v1/tasks", post(submit_task))
        .route("/api/v1/tasks", get(list_tasks))
        .route("/api/v1/tasks/pending", get(get_pending_tasks))
        .route("/api/v1/tasks/:task_id/result", post(report_task_result))
        .with_state(state)
        .layer(CorsLayer::permissive());

    // Start server
    let addr = SocketAddr::from(([0, 0, 0, 0], 8080));
    
    info!("?? Styx Server starting on http://{}", addr);
    info!("Health check: http://{}/health", addr);
    info!("Submit task: POST http://{}/api/v1/tasks", addr);
    info!("List tasks: GET http://{}/api/v1/tasks", addr);
    info!("Pending tasks: GET http://{}/api/v1/tasks/pending", addr);

    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, app).await?;

    Ok(())
}
