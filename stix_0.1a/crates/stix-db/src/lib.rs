//! # STIX Database Layer
//!
//! SQLite-based persistence layer with sqlx for task orchestration,
//! DAG storage, and state management.
//!
//! ## Modules
//!
//! - [`pool`] - Database connection pool
//! - [`models`] - Database models
//! - [`repo`] - Repository layer for CRUD operations
//! - [`error`] - Database error types

#![warn(missing_docs)]

/// Module version
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

// Core modules
pub mod error;
pub mod pool;
pub mod models;
pub mod repo;

// Re-exports
pub use error::DbError;
pub use pool::DbPool;
pub use models::{TaskRow, EdgeRow};
pub use repo::TaskRepo;

/// Result type alias
pub type Result<T> = std::result::Result<T, DbError>;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_version() {
        assert!(!VERSION.is_empty());
    }
}
