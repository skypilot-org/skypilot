//! # Styx Core
//!
//! Core orchestration engine for Styx.
//! Provides task scheduling, resource management, and execution DAGs.

pub mod error;
pub mod resource;
pub mod scheduler;
pub mod task;

pub use error::{Error, Result};
pub use resource::{Resource, ResourceRequirements};
pub use scheduler::{Scheduler, SchedulerConfig, SchedulerEvent, SchedulerStats};
pub use task::{Task, TaskId, TaskPriority, TaskStatus};

/// Core version
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_version() {
        assert!(!VERSION.is_empty());
    }
}

// SkyPilot compatibility modules
pub mod jobs;
pub mod sky_compat;
pub mod spot;
pub mod storage;

pub use jobs::{Job, JobQueue, JobStatus};
pub use sky_compat::{down, exec, launch, start, status, stop, SkyResources, SkyTask};
pub use spot::{RecoveryStrategy, SpotConfig, SpotJob, SpotOps};
pub use storage::{Storage, StorageMode, StorageOps, StorageType};
