use axum::{
    extract::{State, Json},
    http::{StatusCode, HeaderMap},
    response::IntoResponse,
    routing::{post, get},
    Router,
};
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use std::path::PathBuf;
use uuid::Uuid;

use crate::agent::{Agent, Permission};
use crate::locks::LockManager;
use crate::logger::AuditLogger;
use crate::tools::{ToolExecutor, ToolRequest, ToolResponse};

/// Server state
pub struct ServerState {
    pub agents: Arc<dashmap::DashMap<Uuid, Agent>>,
    pub lock_manager: Arc<LockManager>,
    pub audit_logger: Arc<AuditLogger>,
    pub tool_executor: Arc<ToolExecutor>,
}

impl ServerState {
    pub fn new(workspace_root: PathBuf, log_file: Option<PathBuf>) -> Self {
        let lock_manager = LockManager::new();
        let audit_logger = AuditLogger::new(100, log_file);
        let tool_executor = ToolExecutor::new(
            lock_manager.clone(),
            audit_logger.clone(),
            workspace_root,
        );

        Self {
            agents: Arc::new(dashmap::DashMap::new()),
            lock_manager: Arc::new(lock_manager),
            audit_logger: Arc::new(audit_logger),
            tool_executor: Arc::new(tool_executor),
        }
    }
}

/// Request to register a new agent
#[derive(Debug, Deserialize)]
pub struct RegisterAgentRequest {
    pub name: String,
    pub permissions: Vec<Permission>,
}

/// Response with agent token
#[derive(Debug, Serialize)]
pub struct RegisterAgentResponse {
    pub agent_id: Uuid,
    pub token: String,
}

/// Health check response
#[derive(Debug, Serialize)]
pub struct HealthResponse {
    pub status: String,
    pub version: String,
}

/// Extract agent from Authorization header
async fn extract_agent(
    headers: &HeaderMap,
    state: &ServerState,
) -> Result<Agent, StatusCode> {
    let auth_header = headers
        .get("Authorization")
        .and_then(|h| h.to_str().ok())
        .ok_or(StatusCode::UNAUTHORIZED)?;

    if !auth_header.starts_with("Bearer ") {
        return Err(StatusCode::UNAUTHORIZED);
    }

    let token = &auth_header[7..];

    // Find agent by token
    for entry in state.agents.iter() {
        if entry.value().verify_token(token) {
            let mut agent = entry.value().clone();
            agent.update_last_active();
            state.agents.insert(agent.id, agent.clone());
            return Ok(agent);
        }
    }

    Err(StatusCode::UNAUTHORIZED)
}

/// Health check endpoint
async fn health() -> impl IntoResponse {
    Json(HealthResponse {
        status: "healthy".to_string(),
        version: env!("CARGO_PKG_VERSION").to_string(),
    })
}

/// Register a new agent
async fn register_agent(
    State(state): State<Arc<ServerState>>,
    Json(req): Json<RegisterAgentRequest>,
) -> impl IntoResponse {
    // Generate token
    let token = Uuid::new_v4().to_string();
    
    // Create agent
    let agent = Agent::new(req.name, &token, req.permissions);
    let agent_id = agent.id;

    // Store agent
    state.agents.insert(agent_id, agent);

    (
        StatusCode::CREATED,
        Json(RegisterAgentResponse { agent_id, token }),
    )
}

/// Execute a tool
async fn execute_tool(
    State(state): State<Arc<ServerState>>,
    headers: HeaderMap,
    Json(req): Json<ToolRequest>,
) -> impl IntoResponse {
    // Extract and verify agent
    let agent = match extract_agent(&headers, &state).await {
        Ok(agent) => agent,
        Err(status) => return (status, Json(ToolResponse {
            success: false,
            result: None,
            error: Some("Unauthorized".to_string()),
        })),
    };

    // Execute tool
    match state.tool_executor.execute(&agent, req).await {
        Ok(response) => (StatusCode::OK, Json(response)),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(ToolResponse {
                success: false,
                result: None,
                error: Some(e.to_string()),
            }),
        ),
    }
}

/// Get agent's own logs
async fn get_my_logs(
    State(state): State<Arc<ServerState>>,
    headers: HeaderMap,
) -> impl IntoResponse {
    let agent = match extract_agent(&headers, &state).await {
        Ok(agent) => agent,
        Err(status) => return (status, Json(serde_json::json!({"error": "Unauthorized"}))),
    };

    let logs = state.audit_logger.get_agent_logs(agent.id);
    (StatusCode::OK, Json(serde_json::to_value(&logs).unwrap_or_default()))
}

/// Get all logs (requires ViewLogs permission)
async fn get_all_logs(
    State(state): State<Arc<ServerState>>,
    headers: HeaderMap,
) -> impl IntoResponse {
    let agent = match extract_agent(&headers, &state).await {
        Ok(agent) => agent,
        Err(status) => return (status, Json(serde_json::json!({"error": "Unauthorized"}))),
    };

    if !agent.has_permission(&Permission::ViewLogs) {
        return (
            StatusCode::FORBIDDEN,
            Json(serde_json::json!({"error": "Permission denied"})),
        );
    }

    let logs = state.audit_logger.get_all_logs();
    (StatusCode::OK, Json(serde_json::to_value(&logs).unwrap_or_default()))
}

/// Create the server router
pub fn create_router(state: Arc<ServerState>) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/agents/register", post(register_agent))
        .route("/tools/execute", post(execute_tool))
        .route("/logs/my", get(get_my_logs))
        .route("/logs/all", get(get_all_logs))
        .with_state(state)
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::Body;
    use axum::http::Request;
    use tower::ServiceExt;

    #[tokio::test]
    async fn test_health_endpoint() {
        let state = Arc::new(ServerState::new(PathBuf::from("/tmp"), None));
        let app = create_router(state);

        let response = app
            .oneshot(
                Request::builder()
                    .uri("/health")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::OK);
    }
}
