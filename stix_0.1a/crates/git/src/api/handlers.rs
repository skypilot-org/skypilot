//! API handlers

use axum::Json;
use serde_json::{json, Value};

// ========== Users ==========

pub async fn list_users() -> Json<Value> {
    Json(json!({ "users": [] }))
}

pub async fn get_user() -> Json<Value> {
    Json(json!({ "user": {} }))
}

pub async fn get_current_user() -> Json<Value> {
    Json(json!({ "user": {} }))
}

// ========== Repositories ==========

pub async fn create_repo() -> Json<Value> {
    Json(json!({ "repository": {} }))
}

pub async fn get_repo() -> Json<Value> {
    Json(json!({ "repository": {} }))
}

pub async fn delete_repo() -> Json<Value> {
    Json(json!({ "deleted": true }))
}

pub async fn list_branches() -> Json<Value> {
    Json(json!({ "branches": [] }))
}

pub async fn list_tags() -> Json<Value> {
    Json(json!({ "tags": [] }))
}

pub async fn list_commits() -> Json<Value> {
    Json(json!({ "commits": [] }))
}

// ========== Issues ==========

pub async fn list_issues() -> Json<Value> {
    Json(json!({ "issues": [] }))
}

pub async fn create_issue() -> Json<Value> {
    Json(json!({ "issue": {} }))
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
