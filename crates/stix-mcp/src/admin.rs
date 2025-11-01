use axum::{
    extract::{State, Path, Json},
    http::{StatusCode, HeaderMap},
    response::IntoResponse,
    routing::{get, post, patch, delete},
    Router,
};
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use uuid::Uuid;

use crate::agent::{Agent, Permission};
use crate::server::ServerState;

/// Admin authentication (simple password-based for now)
const ADMIN_PASSWORD: &str = "admin123"; // TODO: Load from environment

#[derive(Debug, Deserialize)]
pub struct AdminLoginRequest {
    pub password: String,
}

#[derive(Debug, Serialize)]
pub struct AdminLoginResponse {
    pub token: String,
}

#[derive(Debug, Serialize)]
pub struct Stats {
    pub total_agents: usize,
    pub active_agents: usize,
    pub total_locks: usize,
    pub total_logs: usize,
    pub requests_today: usize,
}

#[derive(Debug, Deserialize)]
pub struct CreateAgentRequest {
    pub name: String,
    pub permissions: Vec<Permission>,
}

#[derive(Debug, Serialize)]
pub struct CreateAgentResponse {
    pub agent_id: Uuid,
    pub token: String,
}

#[derive(Debug, Deserialize)]
pub struct UpdatePermissionsRequest {
    pub permissions: Vec<Permission>,
}

#[derive(Debug, Deserialize)]
pub struct ForceUnlockRequest {
    pub file_path: String,
}

/// Admin authentication check
fn check_admin_auth(headers: &HeaderMap) -> Result<(), StatusCode> {
    let auth_header = headers
        .get("Authorization")
        .and_then(|h| h.to_str().ok())
        .ok_or(StatusCode::UNAUTHORIZED)?;

    if !auth_header.starts_with("Bearer ") {
        return Err(StatusCode::UNAUTHORIZED);
    }

    let token = &auth_header[7..];
    
    // For now, just check if it's a valid admin token
    // In production, use proper JWT or session management
    if token != "admin-token-placeholder" {
        return Err(StatusCode::UNAUTHORIZED);
    }

    Ok(())
}

/// Admin login
async fn admin_login(
    Json(req): Json<AdminLoginRequest>,
) -> Result<impl IntoResponse, StatusCode> {
    if req.password != ADMIN_PASSWORD {
        return Err(StatusCode::UNAUTHORIZED);
    }

    // In production, generate proper JWT token
    Ok((
        StatusCode::OK,
        Json(AdminLoginResponse {
            token: "admin-token-placeholder".to_string(),
        }),
    ))
}

/// List all agents
async fn list_agents(
    State(state): State<Arc<ServerState>>,
    headers: HeaderMap,
) -> Result<impl IntoResponse, StatusCode> {
    check_admin_auth(&headers)?;

    let agents: Vec<Agent> = state
        .agents
        .iter()
        .map(|entry| entry.value().clone())
        .collect();

    Ok((StatusCode::OK, Json(agents)))
}

/// Create new agent
async fn create_agent(
    State(state): State<Arc<ServerState>>,
    headers: HeaderMap,
    Json(req): Json<CreateAgentRequest>,
) -> Result<impl IntoResponse, StatusCode> {
    check_admin_auth(&headers)?;

    let token = Uuid::new_v4().to_string();
    let agent = Agent::new(req.name, &token, req.permissions);
    let agent_id = agent.id;

    state.agents.insert(agent_id, agent);

    Ok((
        StatusCode::CREATED,
        Json(CreateAgentResponse { agent_id, token }),
    ))
}

/// Delete agent
async fn delete_agent(
    State(state): State<Arc<ServerState>>,
    headers: HeaderMap,
    Path(agent_id): Path<Uuid>,
) -> Result<impl IntoResponse, StatusCode> {
    check_admin_auth(&headers)?;

    if state.agents.remove(&agent_id).is_some() {
        Ok(StatusCode::NO_CONTENT)
    } else {
        Err(StatusCode::NOT_FOUND)
    }
}

/// Update agent permissions
async fn update_permissions(
    State(state): State<Arc<ServerState>>,
    headers: HeaderMap,
    Path(agent_id): Path<Uuid>,
    Json(req): Json<UpdatePermissionsRequest>,
) -> Result<impl IntoResponse, StatusCode> {
    check_admin_auth(&headers)?;

    if let Some(mut agent) = state.agents.get_mut(&agent_id) {
        agent.permissions = req.permissions;
        Ok(StatusCode::NO_CONTENT)
    } else {
        Err(StatusCode::NOT_FOUND)
    }
}

/// Regenerate agent token
async fn regenerate_token(
    State(state): State<Arc<ServerState>>,
    headers: HeaderMap,
    Path(agent_id): Path<Uuid>,
) -> Result<impl IntoResponse, StatusCode> {
    check_admin_auth(&headers)?;

    if let Some(mut agent) = state.agents.get_mut(&agent_id) {
        let new_token = Uuid::new_v4().to_string();
        agent.token_hash = Agent::hash_token(&new_token);
        
        Ok((
            StatusCode::OK,
            Json(serde_json::json!({ "token": new_token })),
        ))
    } else {
        Err(StatusCode::NOT_FOUND)
    }
}

/// Get locks
async fn list_locks(
    State(state): State<Arc<ServerState>>,
    headers: HeaderMap,
) -> Result<impl IntoResponse, StatusCode> {
    check_admin_auth(&headers)?;

    let locks = state.lock_manager.list_locks();
    Ok((StatusCode::OK, Json(locks)))
}

/// Force unlock file
async fn force_unlock(
    State(state): State<Arc<ServerState>>,
    headers: HeaderMap,
    Json(req): Json<ForceUnlockRequest>,
) -> Result<impl IntoResponse, StatusCode> {
    check_admin_auth(&headers)?;

    use std::path::PathBuf;
    let path = PathBuf::from(req.file_path);

    match state.lock_manager.force_unlock(&path) {
        Ok(_) => Ok(StatusCode::NO_CONTENT),
        Err(_) => Err(StatusCode::NOT_FOUND),
    }
}

/// Get logs
async fn get_logs(
    State(state): State<Arc<ServerState>>,
    headers: HeaderMap,
) -> Result<impl IntoResponse, StatusCode> {
    check_admin_auth(&headers)?;

    let logs = state.audit_logger.get_all_logs();
    Ok((StatusCode::OK, Json(logs)))
}

/// Get agent logs
async fn get_agent_logs(
    State(state): State<Arc<ServerState>>,
    headers: HeaderMap,
    Path(agent_id): Path<Uuid>,
) -> Result<impl IntoResponse, StatusCode> {
    check_admin_auth(&headers)?;

    let logs = state.audit_logger.get_agent_logs(agent_id);
    Ok((StatusCode::OK, Json(logs)))
}

/// Get stats
async fn get_stats(
    State(state): State<Arc<ServerState>>,
    headers: HeaderMap,
) -> Result<impl IntoResponse, StatusCode> {
    check_admin_auth(&headers)?;

    use chrono::Utc;

    let total_agents = state.agents.len();
    
    // Count active agents (active in last hour)
    let one_hour_ago = Utc::now() - chrono::Duration::hours(1);
    let active_agents = state
        .agents
        .iter()
        .filter(|entry| entry.value().last_active > one_hour_ago)
        .count();

    let total_locks = state.lock_manager.list_locks().len();
    let all_logs = state.audit_logger.get_all_logs();
    let total_logs = all_logs.len();

    // Count requests today
    let today_start = Utc::now().date_naive().and_hms_opt(0, 0, 0).unwrap();
    let today_start_dt = chrono::DateTime::<Utc>::from_naive_utc_and_offset(
        today_start,
        Utc,
    );
    let requests_today = all_logs
        .iter()
        .filter(|log| log.timestamp > today_start_dt)
        .count();

    let stats = Stats {
        total_agents,
        active_agents,
        total_locks,
        total_logs,
        requests_today,
    };

    Ok((StatusCode::OK, Json(stats)))
}

/// Create admin router
pub fn create_admin_router(state: Arc<ServerState>) -> Router {
    Router::new()
        .route("/login", post(admin_login))
        .route("/agents", get(list_agents).post(create_agent))
        .route("/agents/:agent_id", delete(delete_agent))
        .route("/agents/:agent_id/permissions", patch(update_permissions))
        .route("/agents/:agent_id/regenerate-token", post(regenerate_token))
        .route("/agents/:agent_id/logs", get(get_agent_logs))
        .route("/locks", get(list_locks))
        .route("/locks/force-unlock", post(force_unlock))
        .route("/logs", get(get_logs))
        .route("/stats", get(get_stats))
        .with_state(state)
}
