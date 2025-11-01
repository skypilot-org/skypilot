//! Core configuration
//!
//! TODO: Implement configuration management in Week 3

use crate::error::Result;
use serde::{Deserialize, Serialize};

/// Core configuration
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Config {
    /// Configuration version
    pub version: String,
}

impl Config {
    /// Load configuration
    pub fn load() -> Result<Self> {
        // TODO: Load from file
        Ok(Self {
            version: "0.1.0".to_string(),
        })
    }

    /// Get default configuration
    pub fn default() -> Self {
        Self {
            version: "0.1.0".to_string(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_config_creation() {
        let config = Config::default();
        assert_eq!(config.version, "0.1.0");
    }
}
