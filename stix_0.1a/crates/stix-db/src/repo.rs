//! Repository layer for database operations

use crate::{DbError, DbPool, EdgeRow, TaskRow};
use sqlx::Row;

/// Task repository
pub struct TaskRepo {
    pool: DbPool,
}

impl TaskRepo {
    /// Create a new repository
    pub fn new(pool: DbPool) -> Self {
        Self { pool }
    }

    /// Insert a new task
    pub async fn insert_task(&self, task: &TaskRow) -> Result<(), DbError> {
        sqlx::query(
            "INSERT INTO tasks (id, name, status, retries, max_retries, payload, created_at, updated_at)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        )
        .bind(&task.id)
        .bind(&task.name)
        .bind(&task.status)
        .bind(task.retries)
        .bind(task.max_retries)
        .bind(&task.payload)
        .bind(&task.created_at)
        .bind(&task.updated_at)
        .execute(self.pool.inner())
        .await?;
        Ok(())
    }

    /// Update task status
    pub async fn update_task_status(&self, task_id: &str, status: &str) -> Result<(), DbError> {
        let now = time::OffsetDateTime::now_utc().to_string();
        sqlx::query("UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?")
            .bind(status)
            .bind(&now)
            .bind(task_id)
            .execute(self.pool.inner())
            .await?;
        Ok(())
    }

    /// Upsert edge (INSERT OR REPLACE for idempotency)
    pub async fn upsert_edge(&self, edge: &EdgeRow) -> Result<(), DbError> {
        sqlx::query("INSERT OR REPLACE INTO edges (src, dst) VALUES (?, ?)")
            .bind(&edge.src)
            .bind(&edge.dst)
            .execute(self.pool.inner())
            .await?;
        Ok(())
    }

    /// Get ready tasks (no pending dependencies)
    pub async fn get_ready_tasks(&self, limit: i32) -> Result<Vec<TaskRow>, DbError> {
        let tasks = sqlx::query_as::<_, TaskRow>(
            "SELECT t.*
             FROM tasks t
             WHERE t.status = 'PENDING'
             AND NOT EXISTS (
               SELECT 1 FROM edges e
               JOIN tasks dep ON dep.id = e.src
               WHERE e.dst = t.id AND dep.status != 'SUCCESS'
             )
             LIMIT ?"
        )
        .bind(limit)
        .fetch_all(self.pool.inner())
        .await?;
        Ok(tasks)
    }

    /// Load graph for given task IDs
    pub async fn load_graph(&self, task_ids: &[String]) -> Result<Vec<EdgeRow>, DbError> {
        if task_ids.is_empty() {
            return Ok(Vec::new());
        }

        let placeholders = (0..task_ids.len())
            .map(|_| "?")
            .collect::<Vec<_>>()
            .join(",");

        let query = format!(
            "SELECT src, dst FROM edges WHERE src IN ({}) OR dst IN ({})",
            placeholders, placeholders
        );

        let mut q = sqlx::query_as::<_, EdgeRow>(&query);
        for id in task_ids {
            q = q.bind(id);
        }
        for id in task_ids {
            q = q.bind(id);
        }

        let edges = q.fetch_all(self.pool.inner()).await?;
        Ok(edges)
    }

    /// Get task by ID
    pub async fn get_task(&self, task_id: &str) -> Result<Option<TaskRow>, DbError> {
        let task = sqlx::query_as::<_, TaskRow>("SELECT * FROM tasks WHERE id = ?")
            .bind(task_id)
            .fetch_optional(self.pool.inner())
            .await?;
        Ok(task)
    }

    /// Increment retry count
    pub async fn increment_retries(&self, task_id: &str) -> Result<(), DbError> {
        let now = time::OffsetDateTime::now_utc().to_string();
        sqlx::query("UPDATE tasks SET retries = retries + 1, updated_at = ? WHERE id = ?")
            .bind(&now)
            .bind(task_id)
            .execute(self.pool.inner())
            .await?;
        Ok(())
    }

    /// Begin transaction
    pub async fn begin(&self) -> Result<sqlx::Transaction<'_, sqlx::Sqlite>, DbError> {
        let tx = self.pool.inner().begin().await?;
        Ok(tx)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_insert_and_get_task() {
        let pool = DbPool::in_memory().await.unwrap();
        let repo = TaskRepo::new(pool);

        let task = TaskRow::new("task-1".to_string(), "Test Task".to_string(), "PENDING".to_string());
        repo.insert_task(&task).await.unwrap();

        let fetched = repo.get_task("task-1").await.unwrap();
        assert!(fetched.is_some());
        assert_eq!(fetched.unwrap().name, "Test Task");
    }

    #[tokio::test]
    async fn test_update_status() {
        let pool = DbPool::in_memory().await.unwrap();
        let repo = TaskRepo::new(pool);

        let task = TaskRow::new("task-1".to_string(), "Test".to_string(), "PENDING".to_string());
        repo.insert_task(&task).await.unwrap();

        repo.update_task_status("task-1", "RUNNING").await.unwrap();

        let fetched = repo.get_task("task-1").await.unwrap().unwrap();
        assert_eq!(fetched.status, "RUNNING");
    }

    #[tokio::test]
    async fn test_ready_tasks() {
        let pool = DbPool::in_memory().await.unwrap();
        let repo = TaskRepo::new(pool);

        // Task A (no deps)
        let task_a = TaskRow::new("task-a".to_string(), "A".to_string(), "PENDING".to_string());
        repo.insert_task(&task_a).await.unwrap();

        // Task B (depends on A)
        let task_b = TaskRow::new("task-b".to_string(), "B".to_string(), "PENDING".to_string());
        repo.insert_task(&task_b).await.unwrap();
        repo.upsert_edge(&EdgeRow::new("task-a".to_string(), "task-b".to_string())).await.unwrap();

        // Only A should be ready
        let ready = repo.get_ready_tasks(10).await.unwrap();
        assert_eq!(ready.len(), 1);
        assert_eq!(ready[0].id, "task-a");

        // Mark A as SUCCESS
        repo.update_task_status("task-a", "SUCCESS").await.unwrap();

        // Now B should be ready
        let ready = repo.get_ready_tasks(10).await.unwrap();
        assert_eq!(ready.len(), 1);
        assert_eq!(ready[0].id, "task-b");
    }

    #[tokio::test]
    async fn test_load_graph() {
        let pool = DbPool::in_memory().await.unwrap();
        let repo = TaskRepo::new(pool);

        let task_a = TaskRow::new("task-a".to_string(), "A".to_string(), "PENDING".to_string());
        let task_b = TaskRow::new("task-b".to_string(), "B".to_string(), "PENDING".to_string());
        repo.insert_task(&task_a).await.unwrap();
        repo.insert_task(&task_b).await.unwrap();

        repo.upsert_edge(&EdgeRow::new("task-a".to_string(), "task-b".to_string())).await.unwrap();

        let edges = repo.load_graph(&["task-a".to_string(), "task-b".to_string()]).await.unwrap();
        assert_eq!(edges.len(), 1);
        assert_eq!(edges[0].src, "task-a");
        assert_eq!(edges[0].dst, "task-b");
    }
}
