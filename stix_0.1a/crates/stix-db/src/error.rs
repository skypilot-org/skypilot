//! Database error types

use thiserror::Error;

/// Result type alias
pub type Result<T> = std::result::Result<T, DbError>;

/// Database error types
#[derive(Error, Debug)]
pub enum DbError {
    /// SQL error from sqlx
    #[error("Database error: {0}")]
    Sqlx(#[from] sqlx::Error),

    /// Migration error
    #[error("Migration error: {0}")]
    Migration(#[from] sqlx::migrate::MigrateError),

    /// Validation error
    #[error("Validation error: {0}")]
    Validation(String),

    /// Not found error
    #[error("Not found: {0}")]
    NotFound(String),

    /// Generic error
    #[error("{0}")]
    Generic(String),
}

impl DbError {
    /// Create a validation error
    pub fn validation(msg: impl Into<String>) -> Self {
        Self::Validation(msg.into())
    }

    /// Create a not found error
    pub fn not_found(msg: impl Into<String>) -> Self {
        Self::NotFound(msg.into())
    }
}
