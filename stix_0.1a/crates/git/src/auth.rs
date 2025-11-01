//! Authentication & Authorization

use jsonwebtoken::{decode, encode, DecodingKey, EncodingKey, Header, Validation};
use serde::{Deserialize, Serialize};

/// JWT claims
#[derive(Debug, Serialize, Deserialize)]
pub struct Claims {
    pub sub: String, // User ID
    pub username: String,
    pub exp: usize, // Expiration time
}

impl Claims {
    /// Create new claims
    pub fn new(user_id: i64, username: String, expires_in_hours: i64) -> Self {
        let exp =
            (chrono::Utc::now() + chrono::Duration::hours(expires_in_hours)).timestamp() as usize;

        Self {
            sub: user_id.to_string(),
            username,
            exp,
        }
    }
}

/// JWT manager
pub struct JwtManager {
    secret: String,
}

impl JwtManager {
    pub fn new(secret: String) -> Self {
        Self { secret }
    }

    /// Generate JWT token
    pub fn generate(&self, claims: &Claims) -> anyhow::Result<String> {
        let token = encode(
            &Header::default(),
            claims,
            &EncodingKey::from_secret(self.secret.as_bytes()),
        )?;
        Ok(token)
    }

    /// Verify JWT token
    pub fn verify(&self, token: &str) -> anyhow::Result<Claims> {
        let token_data = decode::<Claims>(
            token,
            &DecodingKey::from_secret(self.secret.as_bytes()),
            &Validation::default(),
        )?;
        Ok(token_data.claims)
    }
}

/// Permission types
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Permission {
    Read,
    Write,
    Admin,
}

/// Check if user has permission for repository
pub fn check_permission(
    user_id: i64,
    repo_owner_id: i64,
    repo_is_private: bool,
    required: Permission,
) -> bool {
    // Repository owner has all permissions
    if user_id == repo_owner_id {
        return true;
    }

    // Public repositories can be read by anyone
    if !repo_is_private && required == Permission::Read {
        return true;
    }

    // TODO: Check organization memberships and collaborators

    false
}
