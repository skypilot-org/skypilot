//! Configuration management

use serde::{Deserialize, Serialize};
use std::path::PathBuf;

/// Main configuration structure
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Config {
    /// Server configuration
    pub server: ServerConfig,
    
    /// Database configuration
    pub database: DatabaseConfig,
    
    /// Cloud provider configuration
    pub cloud: CloudConfig,
    
    /// Logging configuration
    pub logging: LoggingConfig,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ServerConfig {
    pub host: String,
    pub port: u16,
    pub grpc_port: u16,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DatabaseConfig {
    pub url: String,
    pub max_connections: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CloudConfig {
    pub aws: Option<AwsConfig>,
    pub gcp: Option<GcpConfig>,
    pub azure: Option<AzureConfig>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AwsConfig {
    pub region: String,
    pub profile: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GcpConfig {
    pub project_id: String,
    pub region: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AzureConfig {
    pub subscription_id: String,
    pub resource_group: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LoggingConfig {
    pub level: String,
    pub format: String,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            server: ServerConfig {
                host: "127.0.0.1".to_string(),
                port: 8080,
                grpc_port: 50051,
            },
            database: DatabaseConfig {
                url: "sqlite://skypilot.db".to_string(),
                max_connections: 10,
            },
            cloud: CloudConfig {
                aws: None,
                gcp: None,
                azure: None,
            },
            logging: LoggingConfig {
                level: "info".to_string(),
                format: "json".to_string(),
            },
        }
    }
}

impl Config {
    /// Load configuration from file
    pub fn load(path: PathBuf) -> anyhow::Result<Self> {
        let content = std::fs::read_to_string(path)?;
        let config: Config = serde_yaml::from_str(&content)?;
        Ok(config)
    }

    /// Load configuration from environment or defaults
    pub fn from_env() -> anyhow::Result<Self> {
        // Try to load from config file first
        let config_path = std::env::var("SKYPILOT_CONFIG")
            .unwrap_or_else(|_| "skypilot.yaml".to_string());
        
        if std::path::Path::new(&config_path).exists() {
            Self::load(PathBuf::from(config_path))
        } else {
            Ok(Self::default())
        }
    }
}
