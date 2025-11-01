//! Configuration loader

use crate::{Config, Error, Result};
use std::path::{Path, PathBuf};
use tokio::fs;

/// Configuration loader
pub struct ConfigLoader;

impl ConfigLoader {
    /// Load configuration from file
    pub async fn load_from_file(path: impl AsRef<Path>) -> Result<Config> {
        let path = path.as_ref();
        
        if !path.exists() {
            return Err(Error::FileNotFound(path.display().to_string()));
        }

        let content = fs::read_to_string(path).await?;
        let config: Config = serde_yaml::from_str(&content)?;
        
        Ok(config)
    }

    /// Load configuration from default locations
    pub async fn load_default() -> Result<Config> {
        // Try .stix/config.yaml in current directory
        let local_path = PathBuf::from(".stix/config.yaml");
        if local_path.exists() {
            return Self::load_from_file(&local_path).await;
        }

        // Try ~/.stix/config.yaml
        if let Ok(home) = std::env::var("HOME") {
            let global_path = PathBuf::from(home).join(".stix/config.yaml");
            if global_path.exists() {
                return Self::load_from_file(&global_path).await;
            }
        }

        // Return default config if no file found
        Ok(Config::default())
    }

    /// Save configuration to file
    pub async fn save_to_file(config: &Config, path: impl AsRef<Path>) -> Result<()> {
        let path = path.as_ref();
        
        // Create parent directory if it doesn't exist
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).await?;
        }

        let yaml = serde_yaml::to_string(config)?;
        fs::write(path, yaml).await?;
        
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::fs;

    #[tokio::test]
    async fn test_load_default() {
        let config = ConfigLoader::load_default().await.unwrap();
        assert_eq!(config.version, "1.0");
    }

    #[tokio::test]
    async fn test_save_and_load() {
        let temp_path = "/tmp/test_stix_config.yaml";
        
        let config = Config::default();
        ConfigLoader::save_to_file(&config, temp_path).await.unwrap();
        
        let loaded = ConfigLoader::load_from_file(temp_path).await.unwrap();
        assert_eq!(loaded.version, config.version);
        
        // Cleanup
        let _ = fs::remove_file(temp_path).await;
    }
}
