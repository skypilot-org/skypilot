//! Main Git server

use anyhow::Result;
use axum::{Router, Server};
use std::net::SocketAddr;
use std::sync::Arc;
use tokio::sync::RwLock;

use crate::api;
use crate::config::Config;
use crate::db::Database;
use crate::ssh::SshServer;
use crate::ui;

/// Git server state
pub struct GitServer {
    config: Arc<Config>,
    db: Arc<Database>,
    ssh_server: Option<SshServer>,
}

impl GitServer {
    /// Create new Git server
    pub async fn new(config: Config) -> Result<Self> {
        // Initialize database
        let db = Database::new(&config.database).await?;

        // Initialize SSH server if enabled
        let ssh_server = if !config.ssh.disabled {
            Some(SshServer::new(&config.ssh).await?)
        } else {
            None
        };

        Ok(Self {
            config: Arc::new(config),
            db: Arc::new(db),
            ssh_server,
        })
    }

    /// Run the server
    pub async fn run(self) -> Result<()> {
        tracing::info!("Starting Styx Git Server...");

        // Start SSH server if enabled
        if let Some(ssh_server) = self.ssh_server {
            let ssh_handle = tokio::spawn(async move {
                if let Err(e) = ssh_server.run().await {
                    tracing::error!("SSH server error: {}", e);
                }
            });
        }

        // Build HTTP router
        let app = self.build_router();

        // Start HTTP server
        let addr = SocketAddr::from((
            self.config.server.host.parse::<std::net::IpAddr>()?,
            self.config.server.http_port,
        ));

        tracing::info!("HTTP server listening on {}", addr);
        tracing::info!("Access at: {}", self.config.server.root_url);

        if !self.config.ssh.disabled {
            tracing::info!("SSH server listening on port {}", self.config.ssh.port);
        }

        axum::Server::bind(&addr)
            .serve(app.into_make_service())
            .await?;

        Ok(())
    }

    /// Build router
    fn build_router(&self) -> Router {
        let state = AppState {
            config: Arc::clone(&self.config),
            db: Arc::clone(&self.db),
        };

        Router::new()
            // API routes
            .nest("/api/v1", api::routes::create_router())
            // UI routes
            .nest("/", ui::routes::create_router())
            // Git HTTP protocol
            .nest("/", crate::http::create_router())
            // Static files
            .nest("/assets", tower_http::services::ServeDir::new("assets"))
            // Health check
            .route("/health", axum::routing::get(|| async { "OK" }))
            .with_state(state)
            .layer(
                tower_http::trace::TraceLayer::new_for_http()
                    .make_span_with(
                        tower_http::trace::DefaultMakeSpan::new().level(tracing::Level::INFO),
                    )
                    .on_response(
                        tower_http::trace::DefaultOnResponse::new().level(tracing::Level::INFO),
                    ),
            )
            .layer(tower_http::cors::CorsLayer::permissive())
    }
}

/// Application state
#[derive(Clone)]
pub struct AppState {
    pub config: Arc<Config>,
    pub db: Arc<Database>,
}
