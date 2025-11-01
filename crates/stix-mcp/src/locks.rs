use serde::{Deserialize, Serialize};
use uuid::Uuid;
use chrono::{DateTime, Utc};
use std::path::PathBuf;
use dashmap::DashMap;
use std::sync::Arc;
use anyhow::{Result, bail};

/// Represents a file lock in the system
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FileLock {
    pub file_path: PathBuf,
    pub owner_id: Uuid,
    pub owner_name: String,
    pub locked_at: DateTime<Utc>,
    pub expires_at: Option<DateTime<Utc>>,
}

/// Manages file locks for conflict prevention
#[derive(Clone)]
pub struct LockManager {
    locks: Arc<DashMap<PathBuf, FileLock>>,
}

impl LockManager {
    pub fn new() -> Self {
        Self {
            locks: Arc::new(DashMap::new()),
        }
    }

    /// Lock a file for exclusive access
    pub fn lock_file(
        &self,
        file_path: PathBuf,
        agent_id: Uuid,
        agent_name: String,
        duration_secs: Option<u64>,
    ) -> Result<()> {
        // Check if file is already locked
        if let Some(existing_lock) = self.locks.get(&file_path) {
            // Check if lock is expired
            if let Some(expires_at) = existing_lock.expires_at {
                if Utc::now() < expires_at {
                    bail!(
                        "File {:?} is already locked by agent {} ({})",
                        file_path,
                        existing_lock.owner_name,
                        existing_lock.owner_id
                    );
                }
            } else {
                bail!(
                    "File {:?} is already locked by agent {} ({}) without expiration",
                    file_path,
                    existing_lock.owner_name,
                    existing_lock.owner_id
                );
            }
        }

        let expires_at = duration_secs.map(|secs| {
            Utc::now() + chrono::Duration::seconds(secs as i64)
        });

        let lock = FileLock {
            file_path: file_path.clone(),
            owner_id: agent_id,
            owner_name: agent_name,
            locked_at: Utc::now(),
            expires_at,
        };

        self.locks.insert(file_path, lock);
        Ok(())
    }

    /// Unlock a file
    pub fn unlock_file(&self, file_path: &PathBuf, agent_id: Uuid) -> Result<()> {
        if let Some(lock) = self.locks.get(file_path) {
            if lock.owner_id != agent_id {
                bail!(
                    "Cannot unlock file {:?}: owned by different agent {} ({})",
                    file_path,
                    lock.owner_name,
                    lock.owner_id
                );
            }
        } else {
            bail!("File {:?} is not locked", file_path);
        }

        self.locks.remove(file_path);
        Ok(())
    }

    /// Check if a file is locked
    pub fn is_locked(&self, file_path: &PathBuf) -> bool {
        if let Some(lock) = self.locks.get(file_path) {
            // Check expiration
            if let Some(expires_at) = lock.expires_at {
                if Utc::now() >= expires_at {
                    drop(lock);
                    self.locks.remove(file_path);
                    return false;
                }
            }
            true
        } else {
            false
        }
    }

    /// Get lock info for a file
    pub fn get_lock(&self, file_path: &PathBuf) -> Option<FileLock> {
        self.locks.get(file_path).map(|lock| lock.clone())
    }

    /// List all active locks
    pub fn list_locks(&self) -> Vec<FileLock> {
        self.locks.iter().map(|entry| entry.value().clone()).collect()
    }

    /// Clean up expired locks
    pub fn cleanup_expired(&self) -> usize {
        let now = Utc::now();
        let mut removed = 0;

        self.locks.retain(|_, lock| {
            if let Some(expires_at) = lock.expires_at {
                if now >= expires_at {
                    removed += 1;
                    return false;
                }
            }
            true
        });

        removed
    }

    /// Force unlock (admin only)
    pub fn force_unlock(&self, file_path: &PathBuf) -> Result<()> {
        if self.locks.remove(file_path).is_some() {
            Ok(())
        } else {
            bail!("File {:?} is not locked", file_path)
        }
    }
}

impl Default for LockManager {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_lock_unlock() {
        let manager = LockManager::new();
        let path = PathBuf::from("test.rs");
        let agent_id = Uuid::new_v4();

        // Lock file
        assert!(manager
            .lock_file(path.clone(), agent_id, "Agent1".to_string(), None)
            .is_ok());
        assert!(manager.is_locked(&path));

        // Try to lock again
        assert!(manager
            .lock_file(path.clone(), Uuid::new_v4(), "Agent2".to_string(), None)
            .is_err());

        // Unlock
        assert!(manager.unlock_file(&path, agent_id).is_ok());
        assert!(!manager.is_locked(&path));
    }

    #[test]
    fn test_expired_locks() {
        let manager = LockManager::new();
        let path = PathBuf::from("test.rs");
        let agent_id = Uuid::new_v4();

        // Lock with 0 second expiration (already expired)
        manager
            .lock_file(path.clone(), agent_id, "Agent1".to_string(), Some(0))
            .unwrap();

        // Should be able to lock again since it's expired
        std::thread::sleep(std::time::Duration::from_millis(100));
        assert!(!manager.is_locked(&path));
    }
}
