use crate::error::DbError;
use crate::models::{EdgeRow, TaskRow, KvRow};
use crate::pool::DbPool;
use sqlx::Row;

/// Repository for database operations
pub struct Repository {
    pool: DbPool,
}

impl Repository {
    /// Create new repository
    pub fn new(pool: DbPool) -> Self {
        Self { pool }
    }

    /// Insert a new task
    pub async fn insert_task(&self, task: &TaskRow) -> Result<(), DbError> {
        sqlx::query(
            r#"
            INSERT INTO tasks (id, name, status, retries, max_retries, payload, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            "#,
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
    pub async fn update_task_status(&self, task_id: &str, status: &str, retries: i32) -> Result<(), DbError> {
        sqlx::query(
            r#"
            UPDATE tasks
            SET status = ?, retries = ?, updated_at = datetime('now')
            WHERE id = ?
            "#,
        )
        .bind(status)
        .bind(retries)
        .bind(task_id)
        .execute(self.pool.inner())
        .await?;
        Ok(())
    }

    /// Upsert an edge (INSERT OR REPLACE)
    pub async fn upsert_edge(&self, edge: &EdgeRow) -> Result<(), DbError> {
        sqlx::query(
            r#"
            INSERT OR REPLACE INTO edges (src, dst)
            VALUES (?, ?)
            "#,
        )
        .bind(&edge.src)
        .bind(&edge.dst)
        .execute(self.pool.inner())
        .await?;
        Ok(())
    }

    /// Get ready tasks (no unsatisfied dependencies)
    pub async fn get_ready_tasks(&self, limit: i64) -> Result<Vec<TaskRow>, DbError> {
        let rows = sqlx::query_as::<_, TaskRow>(
            r#"
            SELECT t.*
            FROM tasks t
            WHERE t.status = 'PENDING'
            AND NOT EXISTS (
                SELECT 1 FROM edges e
                JOIN tasks dep ON dep.id = e.src
                WHERE e.dst = t.id AND dep.status != 'SUCCESS'
            )
            LIMIT ?
            "#,
        )
        .bind(limit)
        .fetch_all(self.pool.inner())
        .await?;
        Ok(rows)
    }

    /// Load graph for given task IDs
    pub async fn load_graph(&self, task_ids: &[String]) -> Result<(Vec<TaskRow>, Vec<EdgeRow>), DbError> {
        let tasks = sqlx::query_as::<_, TaskRow>(
            r#"
            SELECT * FROM tasks WHERE id IN (SELECT value FROM json_each(?))
            "#,
        )
        .bind(serde_json::to_string(task_ids)?)
        .fetch_all(self.pool.inner())
        .await?;

        let edges = sqlx::query_as::<_, EdgeRow>(
            r#"
            SELECT * FROM edges WHERE src IN (SELECT value FROM json_each(?)) OR dst IN (SELECT value FROM json_each(?))
            "#,
        )
        .bind(serde_json::to_string(task_ids)?)
        .bind(serde_json::to_string(task_ids)?)
        .fetch_all(self.pool.inner())
        .await?;

        Ok((tasks, edges))
    }

    /// Get task by ID
    pub async fn get_task(&self, task_id: &str) -> Result<Option<TaskRow>, DbError> {
        let task = sqlx::query_as::<_, TaskRow>(
            "SELECT * FROM tasks WHERE id = ?",
        )
        .bind(task_id)
        .fetch_optional(self.pool.inner())
        .await?;
        Ok(task)
    }

    /// Get all tasks
    pub async fn get_all_tasks(&self) -> Result<Vec<TaskRow>, DbError> {
        let tasks = sqlx::query_as::<_, TaskRow>("SELECT * FROM tasks")
            .fetch_all(self.pool.inner())
            .await?;
        Ok(tasks)
    }

    /// Get all edges
    pub async fn get_all_edges(&self) -> Result<Vec<EdgeRow>, DbError> {
        let edges = sqlx::query_as::<_, EdgeRow>("SELECT * FROM edges")
            .fetch_all(self.pool.inner())
            .await?;
        Ok(edges)
    }

    /// Set key-value pair
    pub async fn set_kv(&self, key: &str, value: &[u8]) -> Result<(), DbError> {
        sqlx::query(
            r#"
            INSERT OR REPLACE INTO kv (k, v, updated_at)
            VALUES (?, ?, datetime('now'))
            "#,
        )
        .bind(key)
        .bind(value)
        .execute(self.pool.inner())
        .await?;
        Ok(())
    }

    /// Get key-value pair
    pub async fn get_kv(&self, key: &str) -> Result<Option<Vec<u8>>, DbError> {
        let row = sqlx::query("SELECT v FROM kv WHERE k = ?")
            .bind(key)
            .fetch_optional(self.pool.inner())
            .await?;

        Ok(row.map(|r| r.get(0)))
    }

    /// Delete key-value pair
    pub async fn delete_kv(&self, key: &str) -> Result<(), DbError> {
        sqlx::query("DELETE FROM kv WHERE k = ?")
            .bind(key)
            .execute(self.pool.inner())
            .await?;
        Ok(())
    }
}
