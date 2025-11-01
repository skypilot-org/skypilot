use crate::error::DbError;
use sqlx::sqlite::SqlitePool;

/// Database connection pool wrapper
#[derive(Clone)]
pub struct DbPool(SqlitePool);

impl DbPool {
    /// Connect to database and run migrations
    pub async fn connect(url: &str) -> Result<Self, DbError> {
        let pool = sqlx::sqlite::SqlitePoolOptions::new()
            .max_connections(8)
            .connect(url)
            .await?;

        // Run migrations
        sqlx::migrate!("./migrations").run(&pool).await?;

        Ok(Self(pool))
    }

    /// Get inner pool reference
    pub fn inner(&self) -> &SqlitePool {
        &self.0
    }
}
