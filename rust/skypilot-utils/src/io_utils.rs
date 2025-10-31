//! I/O utility functions
//!
//! High-performance implementations of file operations:
//! - Reading last N lines efficiently
//! - File hashing with multiple algorithms
//! - Finding free network ports

use pyo3::prelude::*;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::net::TcpListener;
use std::path::Path;

use crate::errors::SkyPilotError;

/// Read the last N lines from a file efficiently.
///
/// This implementation uses a reverse buffer scan to avoid reading the entire file.
///
/// # Arguments
/// * `file_path` - Path to the file to read
/// * `n_lines` - Number of lines to read from the end
///
/// # Returns
/// A list of strings, one per line (without newline characters)
#[pyfunction]
#[pyo3(signature = (file_path, n_lines))]
pub fn read_last_n_lines(file_path: &str, n_lines: usize) -> PyResult<Vec<String>> {
    let path = Path::new(file_path);
    let mut file = File::open(path)?;

    let file_size = file.metadata()?.len();
    if file_size == 0 {
        return Ok(Vec::new());
    }

    // Strategy: Read chunks from the end until we have enough lines
    const CHUNK_SIZE: u64 = 8192;
    let mut buffer = Vec::new();
    let mut lines_found = 0;
    let mut pos = file_size;

    while pos > 0 && lines_found < n_lines {
        let chunk_size = CHUNK_SIZE.min(pos);
        pos -= chunk_size;

        file.seek(SeekFrom::Start(pos))?;
        let mut chunk = vec![0u8; chunk_size as usize];
        file.read_exact(&mut chunk)?;

        // Count newlines in this chunk
        for byte in chunk.iter().rev() {
            if *byte == b'\n' {
                lines_found += 1;
                if lines_found > n_lines {
                    break;
                }
            }
        }

        // Prepend chunk to buffer
        chunk.extend_from_slice(&buffer);
        buffer = chunk;

        if lines_found > n_lines {
            break;
        }
    }

    // Convert buffer to string and split into lines
    let content = String::from_utf8_lossy(&buffer);
    let all_lines: Vec<&str> = content.lines().collect();

    // Return last n_lines
    let start = if all_lines.len() > n_lines {
        all_lines.len() - n_lines
    } else {
        0
    };

    Ok(all_lines[start..].iter().map(|s| s.to_string()).collect())
}

/// Compute hash of a file using specified algorithm.
///
/// # Arguments
/// * `file_path` - Path to the file to hash
/// * `algorithm` - Hash algorithm ("md5", "sha256", or "sha512")
///
/// # Returns
/// Hexadecimal string representation of the hash
#[pyfunction]
#[pyo3(signature = (file_path, algorithm = "sha256"))]
pub fn hash_file(file_path: &str, algorithm: &str) -> PyResult<String> {
    use md5::Md5 as Md5Hasher;
    use sha2::{Digest, Sha256, Sha512};

    let mut file = File::open(file_path)?;
    let mut buffer = [0u8; 8192];

    let hash_hex = match algorithm.to_lowercase().as_str() {
        "md5" => {
            let mut hasher = Md5Hasher::new();
            loop {
                let n = file.read(&mut buffer)?;
                if n == 0 {
                    break;
                }
                hasher.update(&buffer[..n]);
            }
            format!("{:x}", hasher.finalize())
        }
        "sha256" => {
            let mut hasher = Sha256::new();
            loop {
                let n = file.read(&mut buffer)?;
                if n == 0 {
                    break;
                }
                hasher.update(&buffer[..n]);
            }
            format!("{:x}", hasher.finalize())
        }
        "sha512" => {
            let mut hasher = Sha512::new();
            loop {
                let n = file.read(&mut buffer)?;
                if n == 0 {
                    break;
                }
                hasher.update(&buffer[..n]);
            }
            format!("{:x}", hasher.finalize())
        }
        _ => {
            return Err(SkyPilotError::ParseError(format!(
                "Unsupported hash algorithm: {}",
                algorithm
            ))
            .into())
        }
    };

    Ok(hash_hex)
}

/// Find a free TCP port starting from the given port.
///
/// # Arguments
/// * `start_port` - Port number to start searching from
///
/// # Returns
/// First available port number
#[pyfunction]
#[pyo3(signature = (start_port))]
pub fn find_free_port(start_port: u16) -> PyResult<u16> {
    for port in start_port..65535 {
        if let Ok(listener) = TcpListener::bind(("127.0.0.1", port)) {
            drop(listener); // Close immediately
            return Ok(port);
        }
    }
    Err(SkyPilotError::NotFound("No free port available".to_string()).into())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use tempfile::NamedTempFile;

    #[test]
    fn test_read_last_n_lines() {
        let mut file = NamedTempFile::new().unwrap();
        writeln!(file, "line1").unwrap();
        writeln!(file, "line2").unwrap();
        writeln!(file, "line3").unwrap();
        writeln!(file, "line4").unwrap();
        writeln!(file, "line5").unwrap();
        file.flush().unwrap();

        let lines = read_last_n_lines(file.path().to_str().unwrap(), 3).unwrap();
        assert_eq!(lines, vec!["line3", "line4", "line5"]);
    }

    #[test]
    fn test_hash_file_md5() {
        let mut file = NamedTempFile::new().unwrap();
        writeln!(file, "test content").unwrap();
        file.flush().unwrap();

        let hash = hash_file(file.path().to_str().unwrap(), "md5").unwrap();
        assert_eq!(hash.len(), 32); // MD5 produces 32 hex chars
    }

    #[test]
    fn test_find_free_port() {
        let port = find_free_port(10000).unwrap();
        assert!(port >= 10000);
        assert!(port < 65535);
    }
}
