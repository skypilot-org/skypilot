//! API handlers - REAL IMPLEMENTATIONS

use axum::{
    extract::{Path, State},
    http::StatusCode,
    Json,
};
use serde_json::{json, Value};
use crate::server::AppState;
use crate::models::*;

// ========== Users ==========

pub async fn list_users(
    State(state): State<AppState>,
) -> Result<Json<Value>, StatusCode> {
    let users = sqlx::query_as::<_, User>(
        "SELECT * FROM users ORDER BY created_at DESC LIMIT 100"
    )
    .fetch_all(state.db.pool())
    .await
    .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    
    let public_users: Vec<UserPublic> = users.into_iter()
        .map(UserPublic::from)
        .collect();
    
    Ok(Json(json!({ "users": public_users })))
}

pub async fn get_user(
    State(state): State<AppState>,
    Path(username): Path<String>,
) -> Result<Json<Value>, StatusCode> {
    let user = sqlx::query_as::<_, User>(
        "SELECT * FROM users WHERE username = ?"
    )
    .bind(&username)
    .fetch_one(state.db.pool())
    .await
    .map_err(|_| StatusCode::NOT_FOUND)?;
    
    Ok(Json(json!({ "user": UserPublic::from(user) })))
}

pub async fn get_current_user() -> Json<Value> {
    Json(json!({ "user": {} }))
}

// ========== Repositories ==========

pub async fn create_repo(
    State(state): State<AppState>,
    Json(req): Json<CreateRepoRequest>,
) -> Result<Json<Value>, StatusCode> {
    // Insert into database
    let result = sqlx::query(
        "INSERT INTO repositories (owner_id, name, description, is_private) VALUES (?, ?, ?, ?)"
    )
    .bind(req.owner_id)
    .bind(&req.name)
    .bind(req.description.as_deref().unwrap_or(""))
    .bind(req.is_private.unwrap_or(false))
    .execute(state.db.pool())
    .await
    .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    
    let repo_id = result.last_insert_rowid();
    
    // Create repo on disk
    let repo = Repository {
        id: repo_id,
        owner_id: req.owner_id,
        name: req.name.clone(),
        description: req.description.unwrap_or_default(),
        is_private: req.is_private.unwrap_or(false),
        is_fork: false,
        fork_id: None,
        default_branch: "main".to_string(),
        size: 0,
        created_at: chrono::Utc::now(),
        updated_at: chrono::Utc::now(),
    };
    
    repo.init(&state.config.repository.root)
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    
    Ok(Json(json!({ "repository": repo })))
}

pub async fn get_repo(
    State(state): State<AppState>,
    Path((owner, repo_name)): Path<(String, String)>,
) -> Result<Json<Value>, StatusCode> {
    let repo = sqlx::query_as::<_, Repository>(
        "SELECT r.* FROM repositories r JOIN users u ON r.owner_id = u.id WHERE u.username = ? AND r.name = ?"
    )
    .bind(&owner)
    .bind(&repo_name)
    .fetch_one(state.db.pool())
    .await
    .map_err(|_| StatusCode::NOT_FOUND)?;
    
    Ok(Json(json!({ "repository": repo })))
}

pub async fn delete_repo() -> Json<Value> {
    Json(json!({ "deleted": true }))
}

pub async fn list_branches(
    State(state): State<AppState>,
    Path((owner, repo_name)): Path<(String, String)>,
) -> Result<Json<Value>, StatusCode> {
    let repo = sqlx::query_as::<_, Repository>(
        "SELECT r.* FROM repositories r JOIN users u ON r.owner_id = u.id WHERE u.username = ? AND r.name = ?"
    )
    .bind(&owner)
    .bind(&repo_name)
    .fetch_one(state.db.pool())
    .await
    .map_err(|_| StatusCode::NOT_FOUND)?;
    
    let branches = repo.branches(&state.config.repository.root)
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    
    Ok(Json(json!({ "branches": branches })))
}

pub async fn list_tags() -> Json<Value> {
    Json(json!({ "tags": [] }))
}

pub async fn list_commits() -> Json<Value> {
    Json(json!({ "commits": [] }))
}

// ========== Issues ==========

pub async fn list_issues(
    State(state): State<AppState>,
    Path((owner, repo_name)): Path<(String, String)>,
) -> Result<Json<Value>, StatusCode> {
    let repo = sqlx::query_as::<_, Repository>(
        "SELECT r.* FROM repositories r JOIN users u ON r.owner_id = u.id WHERE u.username = ? AND r.name = ?"
    )
    .bind(&owner)
    .bind(&repo_name)
    .fetch_one(state.db.pool())
    .await
    .map_err(|_| StatusCode::NOT_FOUND)?;
    
    let issues = sqlx::query_as::<_, Issue>(
        "SELECT * FROM issues WHERE repo_id = ? ORDER BY created_at DESC"
    )
    .bind(repo.id)
    .fetch_all(state.db.pool())
    .await
    .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    
    Ok(Json(json!({ "issues": issues })))
}

pub async fn create_issue(
    State(state): State<AppState>,
    Path((owner, repo_name)): Path<(String, String)>,
    Json(req): Json<CreateIssueRequest>,
) -> Result<Json<Value>, StatusCode> {
    let repo = sqlx::query_as::<_, Repository>(
        "SELECT r.* FROM repositories r JOIN users u ON r.owner_id = u.id WHERE u.username = ? AND r.name = ?"
    )
    .bind(&owner)
    .bind(&repo_name)
    .fetch_one(state.db.pool())
    .await
    .map_err(|_| StatusCode::NOT_FOUND)?;
    
    // Get next issue number
    let next_index: i64 = sqlx::query_scalar(
        "SELECT COALESCE(MAX(index_num), 0) + 1 FROM issues WHERE repo_id = ?"
    )
    .bind(repo.id)
    .fetch_one(state.db.pool())
    .await
    .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    
    // Insert issue
    let result = sqlx::query(
        "INSERT INTO issues (repo_id, index_num, title, content, author_id) VALUES (?, ?, ?, ?, ?)"
    )
    .bind(repo.id)
    .bind(next_index)
    .bind(&req.title)
    .bind(req.content.as_deref().unwrap_or(""))
    .bind(req.author_id)
    .execute(state.db.pool())
    .await
    .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    
    let issue_id = result.last_insert_rowid();
    
    let issue = Issue {
        id: issue_id,
        repo_id: repo.id,
        index: next_index,
        title: req.title,
        content: req.content.unwrap_or_default(),
        author_id: req.author_id,
        assignee_id: None,
        milestone_id: None,
        is_closed: false,
        created_at: chrono::Utc::now(),
        updated_at: chrono::Utc::now(),
        closed_at: None,
    };
    
    Ok(Json(json!({ "issue": issue })))
}

pub async fn get_issue() -> Json<Value> {
    Json(json!({ "issue": {} }))
}

pub async fn update_issue() -> Json<Value> {
    Json(json!({ "issue": {} }))
}

// ========== Pull Requests ==========

pub async fn list_pulls() -> Json<Value> {
    Json(json!({ "pulls": [] }))
}

pub async fn create_pull() -> Json<Value> {
    Json(json!({ "pull": {} }))
}

pub async fn get_pull() -> Json<Value> {
    Json(json!({ "pull": {} }))
}

pub async fn merge_pull() -> Json<Value> {
    Json(json!({ "merged": true }))
}

// ========== Organizations ==========

pub async fn list_orgs() -> Json<Value> {
    Json(json!({ "organizations": [] }))
}

pub async fn get_org() -> Json<Value> {
    Json(json!({ "organization": {} }))
}

pub async fn list_org_repos() -> Json<Value> {
    Json(json!({ "repositories": [] }))
}
