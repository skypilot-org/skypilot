//! Organization management

use serde::{Deserialize, Serialize};

/// Organization model
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Organization {
    pub id: i64,
    pub name: String,
    pub full_name: String,
    pub description: String,
    pub website: String,
    pub location: String,
    pub avatar: String,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
}

/// Organization member
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OrgMember {
    pub org_id: i64,
    pub user_id: i64,
    pub role: OrgRole,
    pub joined_at: chrono::DateTime<chrono::Utc>,
}

/// Organization roles
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum OrgRole {
    Owner,
    Admin,
    Member,
}
