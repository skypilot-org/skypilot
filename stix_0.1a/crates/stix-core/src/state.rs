//! Global state management
//!
//! TODO: Implement state management in Week 4

/// Cluster registry
pub struct ClusterRegistry;

impl ClusterRegistry {
    /// Create a new cluster registry
    pub fn new() -> Self {
        Self
    }
}

/// Job registry
pub struct JobRegistry;

impl JobRegistry {
    /// Create a new job registry
    pub fn new() -> Self {
        Self
    }
}

/// Storage registry
pub struct StorageRegistry;

impl StorageRegistry {
    /// Create a new storage registry
    pub fn new() -> Self {
        Self
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_registries() {
        let _ = ClusterRegistry::new();
        let _ = JobRegistry::new();
        let _ = StorageRegistry::new();
    }
}
