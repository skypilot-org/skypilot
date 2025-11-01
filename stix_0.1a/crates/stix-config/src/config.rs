//! Configuration structures

use serde::{Deserialize, Serialize};
use std::collections::HashMap;

/// Main configuration structure
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Config {
    /// Version of the config format
    #[serde(default = "default_version")]
    pub version: String,
    
    /// Default cloud provider
    #[serde(default)]
    pub default_cloud: Option<String>,
    
    /// Default region
    #[serde(default)]
    pub default_region: Option<String>,
    
    /// Cloud-specific configurations
    #[serde(default)]
    pub clouds: HashMap<String, CloudConfig>,
    
    /// Global settings
    #[serde(default)]
    pub settings: Settings,
}

fn default_version() -> String {
    "1.0".to_string()
}

/// Cloud-specific configuration
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CloudConfig {
    /// Whether this cloud is enabled
    #[serde(default = "default_true")]
    pub enabled: bool,
    
    /// Default region for this cloud
    #[serde(default)]
    pub default_region: Option<String>,
    
    /// Additional cloud-specific settings
    #[serde(default)]
    pub settings: HashMap<String, serde_yaml::Value>,
}

fn default_true() -> bool {
    true
}

/// Global settings
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Settings {
    /// Enable verbose logging
    #[serde(default)]
    pub verbose: bool,
    
    /// Enable dry-run mode
    #[serde(default)]
    pub dry_run: bool,
    
    /// Default timeout in seconds
    #[serde(default = "default_timeout")]
    pub timeout: u64,
    
    /// Additional custom settings
    #[serde(default)]
    pub custom: HashMap<String, serde_yaml::Value>,
}

fn default_timeout() -> u64 {
    300
}

impl Default for Config {
    fn default() -> Self {
        Self {
            version: default_version(),
            default_cloud: None,
            default_region: None,
            clouds: HashMap::new(),
            settings: Settings::default(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_config_default() {
        let config = Config::default();
        assert_eq!(config.version, "1.0");
        assert_eq!(config.settings.timeout, 300);
    }

    #[test]
    fn test_config_serialization() {
        let config = Config::default();
        let yaml = serde_yaml::to_string(&config).unwrap();
        assert!(yaml.contains("version"));
    }
}
