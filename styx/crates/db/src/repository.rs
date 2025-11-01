//! Repository pattern for database operations

use crate::models::TaskRecord;
use sqlx::{Pool, Sqlite};
use styx_core::{Result, Error};

/// Generic repository trait
#[async_trait::async_trait]
pub trait Repository<T> {
    async fn create(&self, item: &T) -> Result<()>;
    async fn get_by_id(&self, id: &str) -> Result<Option<T>>;
    async fn list(&self) -> Result<Vec<T>>;
    async fn update(&self, item: &T) -> Result<()>;
    async fn delete(&self, id: &str) -> Result<()>;
}

/// Task repository
pub struct TaskRepository {
    pool: Pool<Sqlite>,
}

impl TaskRepository {
    /// Create new task repository
    pub fn new(pool: Pool<Sqlite>) -> Self {
        Self { pool }
    }

    /// Initialize database schema
    pub async fn init_schema(&self) -> Result<()> {
        sqlx::query(
            r#"
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                command TEXT NOT NULL,
                args TEXT NOT NULL,
                status TEXT NOT NULL,
                priority INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                error TEXT
            )
            "#,
        )
        .execute(&self.pool)
        .await
        .map_err(|e| Error::Internal(format!("Failed to create tasks table: {}", e)))?;

        Ok(())
    }
}

#[async_trait::async_trait]
impl Repository<TaskRecord> for TaskRepository {
    async fn create(&self, task: &TaskRecord) -> Result<()> {
        sqlx::query(
            r#"
            INSERT INTO tasks (id, name, command, args, status, priority, created_at, started_at, completed_at, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            "#,
        )
        .bind(&task.id)
        .bind(&task.name)
        .bind(&task.command)
        .bind(&task.args)
        .bind(&task.status)
        .bind(task.priority)
        .bind(task.created_at.to_rfc3339())
        .bind(task.started_at.map(|t| t.to_rfc3339()))
        .bind(task.completed_at.map(|t| t.to_rfc3339()))
        .bind(&task.error)
        .execute(&self.pool)
        .await
        .map_err(|e| Error::Internal(format!("Failed to insert task: {}", e)))?;

        Ok(())
    }

    async fn get_by_id(&self, id: &str) -> Result<Option<TaskRecord>> {
        let task = sqlx::query_as::<_, TaskRecord>(
            r#"
            SELECT * FROM tasks WHERE id = ?
            "#,
        )
        .bind(id)
        .fetch_optional(&self.pool)
        .await
        .map_err(|e| Error::Internal(format!("Failed to fetch task: {}", e)))?;

        Ok(task)
    }

    async fn list(&self) -> Result<Vec<TaskRecord>> {
        let tasks = sqlx::query_as::<_, TaskRecord>(
            r#"
            SELECT * FROM tasks ORDER BY created_at DESC
            "#,
        )
        .fetch_all(&self.pool)
        .await
        .map_err(|e| Error::Internal(format!("Failed to list tasks: {}", e)))?;

        Ok(tasks)
    }

    async fn update(&self, task: &TaskRecord) -> Result<()> {
        sqlx::query(
            r#"
            UPDATE tasks 
            SET name = ?, command = ?, args = ?, status = ?, priority = ?,
                started_at = ?, completed_at = ?, error = ?
            WHERE id = ?
            "#,
        )
        .bind(&task.name)
        .bind(&task.command)
        .bind(&task.args)
        .bind(&task.status)
        .bind(task.priority)
        .bind(task.started_at.map(|t| t.to_rfc3339()))
        .bind(task.completed_at.map(|t| t.to_rfc3339()))
        .bind(&task.error)
        .bind(&task.id)
        .execute(&self.pool)
        .await
        .map_err(|e| Error::Internal(format!("Failed to update task: {}", e)))?;

        Ok(())
    }

    async fn delete(&self, id: &str) -> Result<()> {
        sqlx::query(
            r#"
            DELETE FROM tasks WHERE id = ?
            "#,
        )
        .bind(id)
        .execute(&self.pool)
        .await
        .map_err(|e| Error::Internal(format!("Failed to delete task: {}", e)))?;

        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_repository_trait() {
        // Compilation test
        fn assert_repository<T: Repository<TaskRecord>>() {}
        assert_repository::<TaskRepository>();
    }
}
