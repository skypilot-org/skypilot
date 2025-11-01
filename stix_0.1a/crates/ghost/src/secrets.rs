//! Secrets Manager
//!
//! Encrypts and manages secrets for caves

use aes_gcm::{
    aead::{Aead, KeyInit, OsRng},
    Aes256Gcm, Nonce,
};
use rand::RngCore;
use sha2::{Sha256, Digest};

/// Secrets manager
pub struct SecretsManager {
    cipher: Aes256Gcm,
}

impl SecretsManager {
    /// Create new secrets manager
    pub fn new() -> anyhow::Result<Self> {
        // Generate a key (in production, this should be loaded from secure storage)
        let key = Self::generate_key();
        let cipher = Aes256Gcm::new(&key.into());

        Ok(Self { cipher })
    }

    /// Encrypt a secret
    pub fn encrypt(&self, plaintext: &str) -> anyhow::Result<String> {
        // Generate random nonce
        let mut nonce_bytes = [0u8; 12];
        OsRng.fill_bytes(&mut nonce_bytes);
        let nonce = Nonce::from_slice(&nonce_bytes);

        // Encrypt
        let ciphertext = self.cipher.encrypt(nonce, plaintext.as_bytes())
            .map_err(|e| anyhow::anyhow!("Encryption failed: {}", e))?;

        // Combine nonce + ciphertext and encode as base64
        let mut result = nonce_bytes.to_vec();
        result.extend_from_slice(&ciphertext);
        
        Ok(base64::engine::general_purpose::STANDARD.encode(&result))
    }

    /// Decrypt a secret
    pub fn decrypt(&self, encrypted: &str) -> anyhow::Result<String> {
        // Decode from base64
        let data = base64::engine::general_purpose::STANDARD.decode(encrypted)
            .map_err(|e| anyhow::anyhow!("Base64 decode failed: {}", e))?;

        if data.len() < 12 {
            return Err(anyhow::anyhow!("Invalid encrypted data"));
        }

        // Split nonce and ciphertext
        let (nonce_bytes, ciphertext) = data.split_at(12);
        let nonce = Nonce::from_slice(nonce_bytes);

        // Decrypt
        let plaintext = self.cipher.decrypt(nonce, ciphertext)
            .map_err(|e| anyhow::anyhow!("Decryption failed: {}", e))?;

        String::from_utf8(plaintext)
            .map_err(|e| anyhow::anyhow!("UTF-8 decode failed: {}", e))
    }

    /// Generate encryption key from passphrase
    fn generate_key() -> [u8; 32] {
        // In production, use a secure key management system
        // For now, use a derived key from environment or default
        let passphrase = std::env::var("GHOST_SECRET_KEY")
            .unwrap_or_else(|_| "default-ghost-secret-key-change-me".to_string());

        let mut hasher = Sha256::new();
        hasher.update(passphrase.as_bytes());
        hasher.finalize().into()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_encrypt_decrypt() {
        let manager = SecretsManager::new().unwrap();
        
        let plaintext = "my-secret-password";
        let encrypted = manager.encrypt(plaintext).unwrap();
        let decrypted = manager.decrypt(&encrypted).unwrap();
        
        assert_eq!(plaintext, decrypted);
    }

    #[test]
    fn test_different_encryptions() {
        let manager = SecretsManager::new().unwrap();
        
        let plaintext = "test";
        let enc1 = manager.encrypt(plaintext).unwrap();
        let enc2 = manager.encrypt(plaintext).unwrap();
        
        // Different encryptions due to random nonce
        assert_ne!(enc1, enc2);
        
        // But both decrypt to same value
        assert_eq!(manager.decrypt(&enc1).unwrap(), plaintext);
        assert_eq!(manager.decrypt(&enc2).unwrap(), plaintext);
    }
}
