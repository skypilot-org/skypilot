//! Configuration management

use serde::{Deserialize, Serialize};
use std::path::PathBuf;

/// Main configuration
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Config {
    pub server: ServerConfig,
    pub database: DatabaseConfig,
    pub repository: RepositoryConfig,
    pub security: SecurityConfig,
    pub ssh: SshConfig,
    pub webhook: WebhookConfig,
    pub ui: UiConfig,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            server: ServerConfig::default(),
            database: DatabaseConfig::default(),
            repository: RepositoryConfig::default(),
            security: SecurityConfig::default(),
            ssh: SshConfig::default(),
            webhook: WebhookConfig::default(),
            ui: UiConfig::default(),
        }
    }
}

/// Server configuration
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ServerConfig {
    pub host: String,
    pub http_port: u16,
    pub domain: String,
    pub root_url: String,
    pub cert_file: Option<PathBuf>,
    pub key_file: Option<PathBuf>,
}

impl Default for ServerConfig {
    fn default() -> Self {
        Self {
            host: "0.0.0.0".to_string(),
            http_port: 3000,
            domain: "localhost".to_string(),
            root_url: "http://localhost:3000".to_string(),
            cert_file: None,
            key_file: None,
        }
    }
}

/// Database configuration
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DatabaseConfig {
    pub db_type: DatabaseType,
    pub host: String,
    pub port: u16,
    pub name: String,
    pub user: String,
    pub password: String,
    pub path: PathBuf,
}

impl Default for DatabaseConfig {
    fn default() -> Self {
        Self {
            db_type: DatabaseType::SQLite,
            host: "localhost".to_string(),
            port: 5432,
            name: "styx-git".to_string(),
            user: "styx".to_string(),
            password: "".to_string(),
            path: PathBuf::from("./data/styx-git.db"),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum DatabaseType {
    SQLite,
    PostgreSQL,
    MySQL,
}

/// Repository configuration
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RepositoryConfig {
    pub root: PathBuf,
    pub enable_lfs: bool,
    pub max_creation_limit: i32,
    pub mirror_queue_length: usize,
    pub pull_request_queue_length: usize,
}

impl Default for RepositoryConfig {
    fn default() -> Self {
        Self {
            root: PathBuf::from("./data/repositories"),
            enable_lfs: true,
            max_creation_limit: -1,
            mirror_queue_length: 1000,
            pull_request_queue_length: 1000,
        }
    }
}

/// Security configuration
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SecurityConfig {
    pub secret_key: String,
    pub install_lock: bool,
    pub min_password_length: usize,
    pub disable_git_hooks: bool,
}

impl Default for SecurityConfig {
    fn default() -> Self {
        Self {
            secret_key: uuid::Uuid::new_v4().to_string(),
            install_lock: false,
            min_password_length: 6,
            disable_git_hooks: false,
        }
    }
}

/// SSH configuration
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SshConfig {
    pub disabled: bool,
    pub port: u16,
    pub listen_host: String,
    pub server_key_path: PathBuf,
}

impl Default for SshConfig {
    fn default() -> Self {
        Self {
            disabled: false,
            port: 2222,
            listen_host: "0.0.0.0".to_string(),
            server_key_path: PathBuf::from("./data/ssh/id_rsa"),
        }
    }
}

/// Webhook configuration
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WebhookConfig {
    pub queue_length: usize,
    pub deliver_timeout: u64,
    pub skip_tls_verify: bool,
}

impl Default for WebhookConfig {
    fn default() -> Self {
        Self {
            queue_length: 1000,
            deliver_timeout: 30,
            skip_tls_verify: false,
        }
    }
}

/// UI configuration
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UiConfig {
    pub theme: String,
    pub max_display_file_size: usize,
    pub feed_max_commit_num: usize,
}

impl Default for UiConfig {
    fn default() -> Self {
        Self {
            theme: "default".to_string(),
            max_display_file_size: 8 * 1024 * 1024, // 8MB
            feed_max_commit_num: 5,
        }
    }
}

impl Config {
    /// Load from file
    pub fn from_file(path: impl AsRef<std::path::Path>) -> anyhow::Result<Self> {
        let content = std::fs::read_to_string(path)?;
        let config: Config = toml::from_str(&content)?;
        Ok(config)
    }
    
    /// Save to file
    pub fn to_file(&self, path: impl AsRef<std::path::Path>) -> anyhow::Result<()> {
        let content = toml::to_string_pretty(self)?;
        std::fs::write(path, content)?;
        Ok(())
    }
}
