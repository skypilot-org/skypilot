//! User management

use serde::{Deserialize, Serialize};
use argon2::{Argon2, PasswordHash, PasswordHasher, PasswordVerifier};
use argon2::password_hash::{SaltString, rand_core::OsRng};

/// User model
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct User {
    pub id: i64,
    pub username: String,
    pub email: String,
    #[serde(skip_serializing)]
    pub password_hash: String,
    pub full_name: String,
    pub avatar: String,
    pub is_admin: bool,
    pub is_active: bool,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
}

impl User {
    /// Create new user
    pub fn new(username: String, email: String, password: &str) -> anyhow::Result<Self> {
        let password_hash = Self::hash_password(password)?;
        let now = chrono::Utc::now();
        
        Ok(Self {
            id: 0,
            username,
            email,
            password_hash,
            full_name: String::new(),
            avatar: String::new(),
            is_admin: false,
            is_active: true,
            created_at: now,
            updated_at: now,
        })
    }
    
    /// Hash password
    pub fn hash_password(password: &str) -> anyhow::Result<String> {
        let salt = SaltString::generate(&mut OsRng);
        let argon2 = Argon2::default();
        let hash = argon2.hash_password(password.as_bytes(), &salt)?
            .to_string();
        Ok(hash)
    }
    
    /// Verify password
    pub fn verify_password(&self, password: &str) -> bool {
        let parsed_hash = match PasswordHash::new(&self.password_hash) {
            Ok(h) => h,
            Err(_) => return false,
        };
        
        Argon2::default()
            .verify_password(password.as_bytes(), &parsed_hash)
            .is_ok()
    }
    
    /// Update password
    pub fn update_password(&mut self, password: &str) -> anyhow::Result<()> {
        self.password_hash = Self::hash_password(password)?;
        self.updated_at = chrono::Utc::now();
        Ok(())
    }
}

/// User for API responses (without sensitive data)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UserPublic {
    pub id: i64,
    pub username: String,
    pub email: String,
    pub full_name: String,
    pub avatar: String,
    pub is_admin: bool,
    pub created_at: chrono::DateTime<chrono::Utc>,
}

impl From<User> for UserPublic {
    fn from(user: User) -> Self {
        Self {
            id: user.id,
            username: user.username,
            email: user.email,
            full_name: user.full_name,
            avatar: user.avatar,
            is_admin: user.is_admin,
            created_at: user.created_at,
        }
    }
}

/// SSH key model
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SshKey {
    pub id: i64,
    pub user_id: i64,
    pub name: String,
    pub fingerprint: String,
    pub content: String,
    pub created_at: chrono::DateTime<chrono::Utc>,
}

impl SshKey {
    /// Parse SSH key and generate fingerprint
    pub fn from_content(user_id: i64, name: String, content: String) -> anyhow::Result<Self> {
        // TODO: Validate SSH key format and generate fingerprint
        let fingerprint = format!("SHA256:{}", base64::encode(content.as_bytes()));
        
        Ok(Self {
            id: 0,
            user_id,
            name,
            fingerprint,
            content,
            created_at: chrono::Utc::now(),
        })
    }
}

/// Access token model
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AccessToken {
    pub id: i64,
    pub user_id: i64,
    pub name: String,
    pub token: String,
    pub created_at: chrono::DateTime<chrono::Utc>,
}

impl AccessToken {
    /// Generate new access token
    pub fn generate(user_id: i64, name: String) -> Self {
        let token = uuid::Uuid::new_v4().to_string();
        
        Self {
            id: 0,
            user_id,
            name,
            token,
            created_at: chrono::Utc::now(),
        }
    }
}
