//! Git HTTP protocol (smart HTTP)

use axum::{Router, routing::{get, post}};

/// Create Git HTTP router
pub fn create_router() -> Router {
    Router::new()
        .route("/:username/:repo/info/refs", get(info_refs))
        .route("/:username/:repo/git-upload-pack", post(upload_pack))
        .route("/:username/:repo/git-receive-pack", post(receive_pack))
}

use axum::extract::{Path, Query, State};
use axum::body::Body;
use axum::http::{HeaderMap, StatusCode};
use tokio::process::Command;
use crate::server::AppState;

#[derive(serde::Deserialize)]
struct InfoRefsQuery {
    service: String,
}

/// Handle info/refs request (advertise refs)
async fn info_refs(
    State(state): State<AppState>,
    Path((username, repo)): Path<(String, String)>,
    Query(query): Query<InfoRefsQuery>,
) -> Result<(HeaderMap, Body), StatusCode> {
    // Get repository path
    let repo_path = state.config.repository.root
        .join(&username)
        .join(format!("{}.git", repo));
    
    if !repo_path.exists() {
        return Err(StatusCode::NOT_FOUND);
    }
    
    // Run git command
    let output = Command::new("git")
        .args(&[&query.service, "--stateless-rpc", "--advertise-refs"])
        .arg(&repo_path)
        .output()
        .await
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    
    if !output.status.success() {
        return Err(StatusCode::INTERNAL_SERVER_ERROR);
    }
    
    // Build response
    let mut headers = HeaderMap::new();
    headers.insert(
        "Content-Type",
        format!("application/x-{}-advertisement", query.service)
            .parse()
            .unwrap()
    );
    
    let mut response = format!("001e# service={}\n0000", query.service).into_bytes();
    response.extend_from_slice(&output.stdout);
    
    Ok((headers, Body::from(response)))
}

/// Handle git-upload-pack (git fetch/clone)
async fn upload_pack(
    State(state): State<AppState>,
    Path((username, repo)): Path<(String, String)>,
    body: Body,
) -> Result<(HeaderMap, Body), StatusCode> {
    let repo_path = state.config.repository.root
        .join(&username)
        .join(format!("{}.git", repo));
    
    if !repo_path.exists() {
        return Err(StatusCode::NOT_FOUND);
    }
    
    // Convert body to bytes
    let body_bytes = axum::body::to_bytes(body, usize::MAX)
        .await
        .map_err(|_| StatusCode::BAD_REQUEST)?;
    
    // Run git command
    let mut child = Command::new("git")
        .args(&["upload-pack", "--stateless-rpc"])
        .arg(&repo_path)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .spawn()
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    
    // Write request body to stdin
    if let Some(mut stdin) = child.stdin.take() {
        use tokio::io::AsyncWriteExt;
        stdin.write_all(&body_bytes).await
            .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    }
    
    // Read response
    let output = child.wait_with_output().await
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    
    if !output.status.success() {
        return Err(StatusCode::INTERNAL_SERVER_ERROR);
    }
    
    let mut headers = HeaderMap::new();
    headers.insert(
        "Content-Type",
        "application/x-git-upload-pack-result".parse().unwrap()
    );
    
    Ok((headers, Body::from(output.stdout)))
}

/// Handle git-receive-pack (git push)
async fn receive_pack(
    State(state): State<AppState>,
    Path((username, repo)): Path<(String, String)>,
    body: Body,
) -> Result<(HeaderMap, Body), StatusCode> {
    let repo_path = state.config.repository.root
        .join(&username)
        .join(format!("{}.git", repo));
    
    if !repo_path.exists() {
        return Err(StatusCode::NOT_FOUND);
    }
    
    // Convert body to bytes
    let body_bytes = axum::body::to_bytes(body, usize::MAX)
        .await
        .map_err(|_| StatusCode::BAD_REQUEST)?;
    
    // Run git command
    let mut child = Command::new("git")
        .args(&["receive-pack", "--stateless-rpc"])
        .arg(&repo_path)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .spawn()
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    
    // Write request body to stdin
    if let Some(mut stdin) = child.stdin.take() {
        use tokio::io::AsyncWriteExt;
        stdin.write_all(&body_bytes).await
            .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    }
    
    // Read response
    let output = child.wait_with_output().await
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    
    if !output.status.success() {
        return Err(StatusCode::INTERNAL_SERVER_ERROR);
    }
    
    let mut headers = HeaderMap::new();
    headers.insert(
        "Content-Type",
        "application/x-git-receive-pack-result".parse().unwrap()
    );
    
    Ok((headers, Body::from(output.stdout)))
}
