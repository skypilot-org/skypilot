//! # Styx Git - Self-hosted Git Service
//!
//! Pure Rust implementation of a Git server (inspired by Gogs)
//!
//! ## Features
//! - Git repository hosting
//! - User management
//! - Organization support
//! - Issue tracking
//! - Pull requests
//! - Webhooks
//! - SSH & HTTP(S) support
//! - Web UI
//!
//! ## Example
//! ```rust,no_run
//! use styx_git::{GitServer, Config};
//!
//! #[tokio::main]
//! async fn main() -> anyhow::Result<()> {
//!     let config = Config::default();
//!     let server = GitServer::new(config).await?;
//!     server.run().await?;
//!     Ok(())
//! }
//! ```

pub mod config;
pub mod server;
pub mod repository;
pub mod user;
pub mod organization;
pub mod issue;
pub mod pull_request;
pub mod webhook;
pub mod git;
pub mod ssh;
pub mod http;
pub mod api;
pub mod ui;
pub mod auth;
pub mod db;
pub mod models;

// Re-exports
pub use config::Config;
pub use server::GitServer;
pub use repository::Repository;
pub use user::User;

/// Git service errors
#[derive(Debug, thiserror::Error)]
pub enum GitError {
    #[error("Repository not found: {0}")]
    RepositoryNotFound(String),
    
    #[error("User not found: {0}")]
    UserNotFound(String),
    
    #[error("Permission denied")]
    PermissionDenied,
    
    #[error("Authentication failed")]
    AuthenticationFailed,
    
    #[error("Invalid repository path: {0}")]
    InvalidPath(String),
    
    #[error("Git operation failed: {0}")]
    GitOperationFailed(String),
    
    #[error("Database error: {0}")]
    DatabaseError(String),
    
    #[error(transparent)]
    Other(#[from] anyhow::Error),
}

pub type Result<T> = std::result::Result<T, GitError>;
