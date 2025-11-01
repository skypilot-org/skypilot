//! Database connection pool

use crate::{DbError, Result};
use sqlx::sqlite::{SqlitePool, SqlitePoolOptions};
use tracing::info;

/// Database connection pool wrapper
#[derive(Debug, Clone)]
pub struct DbPool(SqlitePool);

impl DbPool {
    /// Connect to database and run migrations
    pub async fn connect(url: &str) -> Result<Self> {
        info!("Connecting to database: {}", url);
        
        let pool = SqlitePoolOptions::new()
            .max_connections(8)
            .connect(url)
            .await?;
        
        // Run migrations
        info!("Running database migrations");
        sqlx::migrate!("./migrations")
            .run(&pool)
            .await?;
        
        info!("Database initialized successfully");
        Ok(Self(pool))
    }

    /// Create an in-memory database for testing
    pub async fn in_memory() -> Result<Self> {
        Self::connect("sqlite::memory:").await
    }

    /// Get the inner pool
    pub fn inner(&self) -> &SqlitePool {
        &self.0
    }

    /// Close the pool
    pub async fn close(&self) {
        self.0.close().await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_in_memory_connection() {
        let pool = DbPool::in_memory().await.unwrap();
        assert!(pool.inner().is_closed() == false);
    }
}
