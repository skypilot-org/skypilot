//! Global State Management - COMPLETE FUNCTIONAL Implementation
//!
//! Python: sky/global_user_state.py
//! Rust: styx-sky/src/state.rs
//!
//! REAL STATE MANAGEMENT WITH DATABASE!

use serde::{Deserialize, Serialize};
use sqlx::{SqlitePool, Row};
use std::path::PathBuf;
use std::sync::Arc;
use tokio::sync::RwLock;

use crate::clouds::ClusterHandle;
use crate::exceptions::Result;
use crate::resources::Resources;

/// Global user state manager
#[derive(Clone)]
pub struct GlobalUserState {
    db: Arc<SqlitePool>,
    state_dir: PathBuf,
}

/// Cluster record in database
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ClusterRecord {
    pub name: String,
    pub status: ClusterStatus,
    pub handle: ClusterHandle,
    pub resources: Option<Resources>,
    pub created_at: i64,
    pub updated_at: i64,
}

/// Cluster status
#[derive(Debug, Clone, Serialize, Deserialize, sqlx::Type)]
#[sqlx(rename_all = "UPPERCASE")]
pub enum ClusterStatus {
    INIT,
    UP,
    STOPPED,
    TERMINATING,
    TERMINATED,
}

impl GlobalUserState {
    /// Initialize global state
    ///
    /// Python: `global_user_state.init()`
    pub async fn init() -> Result<Self> {
        let state_dir = dirs::home_dir()
            .ok_or_else(|| crate::exceptions::SkyError::InternalError(
                "Cannot find home directory".to_string()
            ))?
            .join(".sky");

        // Create state directory
        std::fs::create_dir_all(&state_dir).map_err(|e| {
            crate::exceptions::SkyError::InternalError(format!("Failed to create state dir: {}", e))
        })?;

        let db_path = state_dir.join("state.db");
        let db_url = format!("sqlite:{}", db_path.display());

        // Create database connection
        let db = SqlitePool::connect(&db_url).await.map_err(|e| {
            crate::exceptions::SkyError::InternalError(format!("Failed to connect to DB: {}", e))
        })?;

        // Create tables
        sqlx::query(
            r#"
            CREATE TABLE IF NOT EXISTS clusters (
                name TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                handle TEXT NOT NULL,
                resources TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            "#,
        )
        .execute(&db)
        .await
        .map_err(|e| {
            crate::exceptions::SkyError::InternalError(format!("Failed to create tables: {}", e))
        })?;

        Ok(Self {
            db: Arc::new(db),
            state_dir,
        })
    }

    /// Add or update a cluster
    ///
    /// Python: `global_user_state.add_or_update_cluster()`
    pub async fn add_or_update_cluster(
        &self,
        name: &str,
        status: ClusterStatus,
        handle: ClusterHandle,
        resources: Option<Resources>,
    ) -> Result<()> {
        let now = chrono::Utc::now().timestamp();
        
        let handle_json = serde_json::to_string(&handle).map_err(|e| {
            crate::exceptions::SkyError::InternalError(format!("Failed to serialize handle: {}", e))
        })?;

        let resources_json = resources
            .as_ref()
            .map(|r| serde_json::to_string(r))
            .transpose()
            .map_err(|e| {
                crate::exceptions::SkyError::InternalError(format!("Failed to serialize resources: {}", e))
            })?;

        let status_str = format!("{:?}", status);

        sqlx::query(
            r#"
            INSERT INTO clusters (name, status, handle, resources, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                status = excluded.status,
                handle = excluded.handle,
                resources = excluded.resources,
                updated_at = excluded.updated_at
            "#,
        )
        .bind(name)
        .bind(&status_str)
        .bind(&handle_json)
        .bind(resources_json)
        .bind(now)
        .bind(now)
        .execute(&*self.db)
        .await
        .map_err(|e| {
            crate::exceptions::SkyError::InternalError(format!("Failed to save cluster: {}", e))
        })?;

        Ok(())
    }

    /// Get a cluster by name
    ///
    /// Python: `global_user_state.get_cluster()`
    pub async fn get_cluster(&self, name: &str) -> Result<Option<ClusterRecord>> {
        let row = sqlx::query(
            r#"
            SELECT name, status, handle, resources, created_at, updated_at
            FROM clusters
            WHERE name = ?
            "#,
        )
        .bind(name)
        .fetch_optional(&*self.db)
        .await
        .map_err(|e| {
            crate::exceptions::SkyError::InternalError(format!("Failed to fetch cluster: {}", e))
        })?;

        if let Some(row) = row {
            let handle_json: String = row.get("handle");
            let handle: ClusterHandle = serde_json::from_str(&handle_json).map_err(|e| {
                crate::exceptions::SkyError::InternalError(format!("Failed to parse handle: {}", e))
            })?;

            let resources_json: Option<String> = row.get("resources");
            let resources = resources_json
                .map(|json| serde_json::from_str(&json))
                .transpose()
                .map_err(|e| {
                    crate::exceptions::SkyError::InternalError(format!("Failed to parse resources: {}", e))
                })?;

            let status_str: String = row.get("status");
            let status = match status_str.as_str() {
                "INIT" => ClusterStatus::INIT,
                "UP" => ClusterStatus::UP,
                "STOPPED" => ClusterStatus::STOPPED,
                "TERMINATING" => ClusterStatus::TERMINATING,
                "TERMINATED" => ClusterStatus::TERMINATED,
                _ => ClusterStatus::INIT,
            };

            Ok(Some(ClusterRecord {
                name: row.get("name"),
                status,
                handle,
                resources,
                created_at: row.get("created_at"),
                updated_at: row.get("updated_at"),
            }))
        } else {
            Ok(None)
        }
    }

    /// Get all clusters
    ///
    /// Python: `global_user_state.get_clusters()`
    pub async fn get_clusters(&self) -> Result<Vec<ClusterRecord>> {
        let rows = sqlx::query(
            r#"
            SELECT name, status, handle, resources, created_at, updated_at
            FROM clusters
            WHERE status != 'TERMINATED'
            ORDER BY updated_at DESC
            "#,
        )
        .fetch_all(&*self.db)
        .await
        .map_err(|e| {
            crate::exceptions::SkyError::InternalError(format!("Failed to fetch clusters: {}", e))
        })?;

        let mut clusters = Vec::new();
        
        for row in rows {
            let handle_json: String = row.get("handle");
            let handle: ClusterHandle = serde_json::from_str(&handle_json).unwrap_or_default();

            let resources_json: Option<String> = row.get("resources");
            let resources = resources_json
                .and_then(|json| serde_json::from_str(&json).ok());

            let status_str: String = row.get("status");
            let status = match status_str.as_str() {
                "INIT" => ClusterStatus::INIT,
                "UP" => ClusterStatus::UP,
                "STOPPED" => ClusterStatus::STOPPED,
                "TERMINATING" => ClusterStatus::TERMINATING,
                "TERMINATED" => ClusterStatus::TERMINATED,
                _ => ClusterStatus::INIT,
            };

            clusters.push(ClusterRecord {
                name: row.get("name"),
                status,
                handle,
                resources,
                created_at: row.get("created_at"),
                updated_at: row.get("updated_at"),
            });
        }

        Ok(clusters)
    }

    /// Remove a cluster
    ///
    /// Python: `global_user_state.remove_cluster()`
    pub async fn remove_cluster(&self, name: &str) -> Result<()> {
        sqlx::query("DELETE FROM clusters WHERE name = ?")
            .bind(name)
            .execute(&*self.db)
            .await
            .map_err(|e| {
                crate::exceptions::SkyError::InternalError(format!("Failed to remove cluster: {}", e))
            })?;

        Ok(())
    }
}

impl Default for ClusterHandle {
    fn default() -> Self {
        Self {
            cloud: String::new(),
            cluster_id: String::new(),
            region: None,
            head_ip: None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_global_state() {
        let state = GlobalUserState::init().await.unwrap();
        
        let handle = ClusterHandle {
            cloud: "aws".to_string(),
            cluster_id: "test-123".to_string(),
            region: Some("us-east-1".to_string()),
            head_ip: Some("1.2.3.4".to_string()),
        };

        state
            .add_or_update_cluster("test-cluster", ClusterStatus::UP, handle.clone(), None)
            .await
            .unwrap();

        let cluster = state.get_cluster("test-cluster").await.unwrap();
        assert!(cluster.is_some());
        
        let cluster = cluster.unwrap();
        assert_eq!(cluster.name, "test-cluster");
        assert_eq!(cluster.handle.cloud, "aws");
    }
}
