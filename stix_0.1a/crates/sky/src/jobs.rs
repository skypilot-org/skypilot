//! Job management
//!
//! A lightweight in-memory job queue that mirrors SkyPilot's managed job APIs.
//! This allows the core crate to expose `queue`/`cancel` without depending on
//! an external scheduler while keeping the surface area compatible.

use std::collections::HashMap;

use chrono::{DateTime, Utc};
use once_cell::sync::Lazy;
use tokio::sync::RwLock;
use uuid::Uuid;

use crate::exceptions::{Result, SkyError};

/// Internal storage for managed jobs.
static JOBS: Lazy<RwLock<HashMap<String, ManagedJob>>> = Lazy::new(|| RwLock::new(HashMap::new()));

/// Managed job record.
#[derive(Debug, Clone)]
pub struct ManagedJob {
    pub id: String,
    pub name: String,
    pub cluster: String,
    pub status: String,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

/// Submit a new job to the queue.
pub async fn submit(name: impl Into<String>, cluster: impl Into<String>) -> Result<ManagedJob> {
    let job = ManagedJob {
        id: Uuid::new_v4().to_string(),
        name: name.into(),
        cluster: cluster.into(),
        status: "submitted".to_string(),
        created_at: Utc::now(),
        updated_at: Utc::now(),
    };

    JOBS.write().await.insert(job.id.clone(), job.clone());
    Ok(job)
}

/// List all managed jobs.
pub async fn list() -> Result<Vec<ManagedJob>> {
    let jobs = JOBS.read().await;
    Ok(jobs.values().cloned().collect())
}

/// Update the status of a job.
pub async fn update_status(id: &str, status: impl Into<String>) -> Result<()> {
    let mut jobs = JOBS.write().await;

    if let Some(job) = jobs.get_mut(id) {
        job.status = status.into();
        job.updated_at = Utc::now();
        Ok(())
    } else {
        Err(SkyError::JobError(format!("Job '{}' not found", id)))
    }
}

/// Cancel a managed job.
pub async fn cancel(id: &str) -> Result<()> {
    let mut jobs = JOBS.write().await;

    if let Some(job) = jobs.get_mut(id) {
        job.status = "cancelled".to_string();
        job.updated_at = Utc::now();
        Ok(())
    } else {
        Err(SkyError::JobError(format!("Job '{}' not found", id)))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_job_lifecycle() {
        let job = submit("train", "cluster-1").await.unwrap();
        assert_eq!(job.status, "submitted");

        update_status(&job.id, "running").await.unwrap();
        let jobs = list().await.unwrap();
        assert_eq!(jobs.len(), 1);
        assert_eq!(jobs[0].status, "running");

        cancel(&job.id).await.unwrap();
        let jobs = list().await.unwrap();
        assert_eq!(jobs[0].status, "cancelled");
    }
}
