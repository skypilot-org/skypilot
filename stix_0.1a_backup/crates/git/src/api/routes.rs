//! API routes

use super::handlers;
use axum::{
    routing::{delete, get, patch, post},
    Router,
};

pub fn create_router() -> Router {
    Router::new()
        // Users
        .route("/users", get(handlers::list_users))
        .route("/users/:username", get(handlers::get_user))
        .route("/user", get(handlers::get_current_user))
        // Repositories
        .route("/repos", post(handlers::create_repo))
        .route("/repos/:owner/:repo", get(handlers::get_repo))
        .route("/repos/:owner/:repo", delete(handlers::delete_repo))
        .route("/repos/:owner/:repo/branches", get(handlers::list_branches))
        .route("/repos/:owner/:repo/tags", get(handlers::list_tags))
        .route("/repos/:owner/:repo/commits", get(handlers::list_commits))
        // Issues
        .route("/repos/:owner/:repo/issues", get(handlers::list_issues))
        .route("/repos/:owner/:repo/issues", post(handlers::create_issue))
        .route(
            "/repos/:owner/:repo/issues/:number",
            get(handlers::get_issue),
        )
        .route(
            "/repos/:owner/:repo/issues/:number",
            patch(handlers::update_issue),
        )
        // Pull Requests
        .route("/repos/:owner/:repo/pulls", get(handlers::list_pulls))
        .route("/repos/:owner/:repo/pulls", post(handlers::create_pull))
        .route("/repos/:owner/:repo/pulls/:number", get(handlers::get_pull))
        .route(
            "/repos/:owner/:repo/pulls/:number/merge",
            post(handlers::merge_pull),
        )
        // Organizations
        .route("/orgs", get(handlers::list_orgs))
        .route("/orgs/:org", get(handlers::get_org))
        .route("/orgs/:org/repos", get(handlers::list_org_repos))
}
