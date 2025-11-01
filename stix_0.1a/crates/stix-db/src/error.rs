use thiserror::Error;

/// Database operation errors
#[derive(Error, Debug)]
pub enum DbError {
    #[error("SQLx error: {0}")]
    Sqlx(#[from] sqlx::Error),

    #[error("Validation error: {0}")]
    Validation(String),

    #[error("Not found: {0}")]
    NotFound(String),

    #[error("Migration error: {0}")]
    Migration(#[from] sqlx::migrate::MigrateError),

    #[error("Serialization error: {0}")]
    Serde(#[from] serde_json::Error),
}
