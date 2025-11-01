//! Configuration manager

use crate::{Config, ConfigLoader, Result};
use std::path::Path;
use std::sync::Arc;
use tokio::sync::RwLock;

/// Configuration manager with caching
#[derive(Debug, Clone)]
pub struct ConfigManager {
    /// Cached configuration
    config: Arc<RwLock<Option<Config>>>,
}

impl Default for ConfigManager {
    fn default() -> Self {
        Self::new()
    }
}

impl ConfigManager {
    /// Create a new configuration manager
    pub fn new() -> Self {
        Self {
            config: Arc::new(RwLock::new(None)),
        }
    }

    /// Get the current configuration, loading if necessary
    pub async fn get_config(&self) -> Result<Config> {
        // Check if cached
        {
            let config = self.config.read().await;
            if let Some(cfg) = &*config {
                return Ok(cfg.clone());
            }
        }

        // Load configuration
        let cfg = ConfigLoader::load_default().await?;

        // Cache it
        {
            let mut config = self.config.write().await;
            *config = Some(cfg.clone());
        }

        Ok(cfg)
    }

    /// Load configuration from specific path
    pub async fn load_from_path(&self, path: impl AsRef<Path>) -> Result<Config> {
        let cfg = ConfigLoader::load_from_file(path).await?;
        
        // Cache it
        {
            let mut config = self.config.write().await;
            *config = Some(cfg.clone());
        }

        Ok(cfg)
    }

    /// Save configuration to specific path
    pub async fn save_to_path(&self, path: impl AsRef<Path>) -> Result<()> {
        let config = self.get_config().await?;
        ConfigLoader::save_to_file(&config, path).await
    }

    /// Clear cached configuration
    pub async fn clear_cache(&self) {
        let mut config = self.config.write().await;
        *config = None;
    }

    /// Reload configuration
    pub async fn reload(&self) -> Result<Config> {
        self.clear_cache().await;
        self.get_config().await
    }

    /// Merge environment-specific overrides
    pub async fn merge_environment(&self, env: &str) -> Result<()> {
        // TODO: Implement environment-specific configuration merging
        tracing::debug!("Merging environment: {}", env);
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_config_manager() {
        let manager = ConfigManager::new();
        
        let config = manager.get_config().await.unwrap();
        assert_eq!(config.version, "1.0");
        
        // Second call should use cache
        let config2 = manager.get_config().await.unwrap();
        assert_eq!(config.version, config2.version);
    }

    #[tokio::test]
    async fn test_config_manager_reload() {
        let manager = ConfigManager::new();
        
        let _ = manager.get_config().await.unwrap();
        let config = manager.reload().await.unwrap();
        assert_eq!(config.version, "1.0");
    }
}
