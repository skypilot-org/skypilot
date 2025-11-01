use serde::{Deserialize, Serialize};
use uuid::Uuid;
use chrono::{DateTime, Utc};

/// Represents a KI-Agent connected to the MCP server
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Agent {
    pub id: Uuid,
    pub name: String,
    pub token_hash: String,
    pub permissions: Vec<Permission>,
    pub created_at: DateTime<Utc>,
    pub last_active: DateTime<Utc>,
}

/// Permission types for agents
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Hash)]
pub enum Permission {
    ReadFiles,
    WriteFiles,
    LockFiles,
    UpdateProgress,
    CreateTasks,
    SearchCodebase,
    ViewLogs,
}

impl Agent {
    pub fn new(name: String, token: &str, permissions: Vec<Permission>) -> Self {
        Self {
            id: Uuid::new_v4(),
            name,
            token_hash: Self::hash_token(token),
            permissions,
            created_at: Utc::now(),
            last_active: Utc::now(),
        }
    }

    pub fn hash_token(token: &str) -> String {
        use sha2::{Sha256, Digest};
        let mut hasher = Sha256::new();
        hasher.update(token.as_bytes());
        hex::encode(hasher.finalize())
    }

    pub fn verify_token(&self, token: &str) -> bool {
        self.token_hash == Self::hash_token(token)
    }

    pub fn has_permission(&self, permission: &Permission) -> bool {
        self.permissions.contains(permission)
    }

    pub fn update_last_active(&mut self) {
        self.last_active = Utc::now();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_agent_creation() {
        let agent = Agent::new(
            "TestAgent".to_string(),
            "secret_token",
            vec![Permission::ReadFiles],
        );
        assert_eq!(agent.name, "TestAgent");
        assert!(agent.verify_token("secret_token"));
        assert!(!agent.verify_token("wrong_token"));
    }

    #[test]
    fn test_permissions() {
        let agent = Agent::new(
            "TestAgent".to_string(),
            "token",
            vec![Permission::ReadFiles, Permission::WriteFiles],
        );
        assert!(agent.has_permission(&Permission::ReadFiles));
        assert!(agent.has_permission(&Permission::WriteFiles));
        assert!(!agent.has_permission(&Permission::LockFiles));
    }
}
