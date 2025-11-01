//! Database layer

use anyhow::Result;
use sqlx::{SqlitePool, sqlite::SqliteConnectOptions};
use std::str::FromStr;
use crate::config::DatabaseConfig;

/// Database connection pool
pub struct Database {
    pool: SqlitePool,
}

impl Database {
    /// Create new database connection
    pub async fn new(config: &DatabaseConfig) -> Result<Self> {
        // Create parent directory if it doesn't exist
        if let Some(parent) = config.path.parent() {
            tokio::fs::create_dir_all(parent).await?;
        }
        
        let options = SqliteConnectOptions::from_str(&format!("sqlite:{}", config.path.display()))?
            .create_if_missing(true);
        
        let pool = SqlitePool::connect_with(options).await?;
        
        // Run migrations
        Self::migrate(&pool).await?;
        
        Ok(Self { pool })
    }
    
    /// Run database migrations
    async fn migrate(pool: &SqlitePool) -> Result<()> {
        sqlx::query(r#"
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                full_name TEXT,
                avatar TEXT,
                is_admin BOOLEAN DEFAULT 0,
                is_active BOOLEAN DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        "#).execute(pool).await?;
        
        sqlx::query(r#"
            CREATE TABLE IF NOT EXISTS repositories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                is_private BOOLEAN DEFAULT 0,
                is_fork BOOLEAN DEFAULT 0,
                fork_id INTEGER,
                default_branch TEXT DEFAULT 'main',
                size INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(owner_id) REFERENCES users(id),
                UNIQUE(owner_id, name)
            )
        "#).execute(pool).await?;
        
        sqlx::query(r#"
            CREATE TABLE IF NOT EXISTS issues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_id INTEGER NOT NULL,
                index_num INTEGER NOT NULL,
                title TEXT NOT NULL,
                content TEXT,
                author_id INTEGER NOT NULL,
                assignee_id INTEGER,
                milestone_id INTEGER,
                is_closed BOOLEAN DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                closed_at DATETIME,
                FOREIGN KEY(repo_id) REFERENCES repositories(id),
                FOREIGN KEY(author_id) REFERENCES users(id),
                UNIQUE(repo_id, index_num)
            )
        "#).execute(pool).await?;
        
        sqlx::query(r#"
            CREATE TABLE IF NOT EXISTS pull_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_id INTEGER NOT NULL,
                index_num INTEGER NOT NULL,
                title TEXT NOT NULL,
                content TEXT,
                head_branch TEXT NOT NULL,
                base_branch TEXT NOT NULL,
                author_id INTEGER NOT NULL,
                assignee_id INTEGER,
                is_merged BOOLEAN DEFAULT 0,
                is_closed BOOLEAN DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                merged_at DATETIME,
                closed_at DATETIME,
                FOREIGN KEY(repo_id) REFERENCES repositories(id),
                FOREIGN KEY(author_id) REFERENCES users(id),
                UNIQUE(repo_id, index_num)
            )
        "#).execute(pool).await?;
        
        sqlx::query(r#"
            CREATE TABLE IF NOT EXISTS ssh_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        "#).execute(pool).await?;
        
        sqlx::query(r#"
            CREATE TABLE IF NOT EXISTS webhooks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_id INTEGER NOT NULL,
                url TEXT NOT NULL,
                secret TEXT NOT NULL,
                events TEXT NOT NULL,
                is_active BOOLEAN DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(repo_id) REFERENCES repositories(id)
            )
        "#).execute(pool).await?;
        
        Ok(())
    }
    
    pub fn pool(&self) -> &SqlitePool {
        &self.pool
    }
}
