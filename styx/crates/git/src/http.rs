//! Git HTTP protocol (smart HTTP)

use axum::{Router, routing::{get, post}};

/// Create Git HTTP router
pub fn create_router() -> Router {
    Router::new()
        .route("/:username/:repo/info/refs", get(info_refs))
        .route("/:username/:repo/git-upload-pack", post(upload_pack))
        .route("/:username/:repo/git-receive-pack", post(receive_pack))
}

/// Handle info/refs request
async fn info_refs() -> &'static str {
    // TODO: Implement Git smart HTTP protocol
    "# service=git-upload-pack\n"
}

/// Handle git-upload-pack (git fetch/clone)
async fn upload_pack() -> &'static str {
    // TODO: Implement git-upload-pack
    "OK"
}

/// Handle git-receive-pack (git push)
async fn receive_pack() -> &'static str {
    // TODO: Implement git-receive-pack
    "OK"
}
