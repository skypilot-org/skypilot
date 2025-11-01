//! UI routes

use axum::{Router, routing::get, response::Html};

pub fn create_router() -> Router {
    Router::new()
        .route("/", get(index))
        .route("/explore", get(explore))
        .route("/:username", get(user_profile))
        .route("/:username/:repo", get(repo_view))
        .route("/:username/:repo/issues", get(issues))
        .route("/:username/:repo/pulls", get(pulls))
}

async fn index() -> Html<&'static str> {
    Html(r#"
<!DOCTYPE html>
<html>
<head>
    <title>Styx Git</title>
    <style>
        body { font-family: sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; }
        h1 { color: #333; }
        .nav { background: #f5f5f5; padding: 10px; margin-bottom: 20px; }
        .nav a { margin-right: 20px; text-decoration: none; color: #0066cc; }
    </style>
</head>
<body>
    <div class="nav">
        <a href="/">Home</a>
        <a href="/explore">Explore</a>
    </div>
    <h1>?? Styx Git</h1>
    <p>Self-hosted Git service powered by Rust</p>
    <h2>Features</h2>
    <ul>
        <li>? Git repository hosting</li>
        <li>? User management</li>
        <li>? Organizations</li>
        <li>? Issue tracking</li>
        <li>? Pull requests</li>
        <li>? Webhooks</li>
        <li>? SSH & HTTP(S) support</li>
    </ul>
</body>
</html>
    "#)
}

async fn explore() -> Html<&'static str> {
    Html("<h1>Explore Repositories</h1>")
}

async fn user_profile() -> Html<&'static str> {
    Html("<h1>User Profile</h1>")
}

async fn repo_view() -> Html<&'static str> {
    Html("<h1>Repository View</h1>")
}

async fn issues() -> Html<&'static str> {
    Html("<h1>Issues</h1>")
}

async fn pulls() -> Html<&'static str> {
    Html("<h1>Pull Requests</h1>")
}
