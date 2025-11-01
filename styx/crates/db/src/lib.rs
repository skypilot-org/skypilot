//! # Styx Database
//!
//! Persistence layer for Styx using SQLx and SeaORM.

pub mod models;
pub mod repository;
pub mod migrations;

pub use repository::{Repository, TaskRepository};

/// Database version
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

/// Database connection string
pub fn default_connection_string() -> String {
    std::env::var("DATABASE_URL")
        .unwrap_or_else(|_| "sqlite://styx.db".to_string())
}
