//! Process and resource management utilities
//!
//! High-performance process management functions:
//! - Parallel thread calculation
//! - Process alive checks
//! - Worker estimation for file operations

use pyo3::prelude::*;
use std::cmp::max;
use std::fs;

use crate::errors::SkyPilotError;
use crate::system_utils::get_cpu_count;

/// Get the number of threads to use for parallel execution.
///
/// Calculates optimal thread count based on CPU count and cloud provider.
/// Kubernetes environments get 4x multiplier due to better resource isolation.
///
/// # Arguments
/// * `cloud_str` - Optional cloud provider name (e.g., "kubernetes")
///
/// # Returns
/// Number of threads (minimum 4, typically cpu_count - 1)
#[pyfunction]
#[pyo3(signature = (cloud_str = None))]
pub fn get_parallel_threads(cloud_str: Option<&str>) -> PyResult<usize> {
    let cpu_count = get_cpu_count()?;

    // Kubernetes uses 4x multiplier for better parallelism
    let multiplier = match cloud_str {
        Some(s) if s.to_lowercase() == "kubernetes" => 4,
        _ => 1,
    };

    // At least 4 threads, but leave one CPU for system
    let threads = max(4, cpu_count.saturating_sub(1)) * multiplier;

    Ok(threads)
}

/// Check if a process is alive.
///
/// Uses direct system calls for fast process existence check.
///
/// # Arguments
/// * `pid` - Process ID to check
///
/// # Returns
/// `true` if process exists and is running, `false` otherwise
#[pyfunction]
#[pyo3(signature = (pid))]
pub fn is_process_alive(pid: i32) -> PyResult<bool> {
    // On Unix, we can use kill(pid, 0) to check if process exists
    // without actually sending a signal
    #[cfg(unix)]
    {
        use nix::sys::signal::kill;
        use nix::unistd::Pid;

        let pid = Pid::from_raw(pid);
        match kill(pid, None) {
            Ok(_) => Ok(true),
            Err(nix::errno::Errno::ESRCH) => Ok(false), // No such process
            Err(nix::errno::Errno::EPERM) => Ok(true),  // Process exists but no permission
            Err(e) => Err(SkyPilotError::SystemError(format!(
                "Failed to check process {}: {}",
                pid, e
            ))
            .into()),
        }
    }

    #[cfg(not(unix))]
    {
        // Fallback for non-Unix systems
        Err(SkyPilotError::SystemError(
            "is_process_alive not supported on this platform".to_string(),
        )
        .into())
    }
}

/// Estimate maximum workers for file mount operations.
///
/// Calculates optimal number of parallel workers based on:
/// - File descriptor limits
/// - Number of files to process
/// - Available CPU cores
///
/// # Arguments
/// * `num_sources` - Number of source directories/files
/// * `estimated_files_per_source` - Estimated files per source (default: 100)
///
/// # Returns
/// Optimal number of workers (between 1 and parallel_threads)
#[pyfunction]
#[pyo3(signature = (num_sources, estimated_files_per_source = 100, cloud_str = None))]
pub fn get_max_workers_for_file_mounts(
    num_sources: usize,
    estimated_files_per_source: usize,
    cloud_str: Option<&str>,
) -> PyResult<usize> {
    // Get file descriptor limits
    let (soft_limit, _hard_limit) = get_fd_limits()?;

    // Estimate FDs needed per rsync operation
    // Base overhead + files * FD per file
    // Also consider number of sources
    let fd_per_rsync = 5 + (estimated_files_per_source / 20).max(5) + (num_sources / 10);

    // Reserve FDs for system and other processes
    const FD_RESERVE: u64 = 100;

    // Calculate max workers based on FD limits
    let max_workers_fd = ((soft_limit.saturating_sub(FD_RESERVE)) / (fd_per_rsync as u64)) as usize;

    // Get parallel thread limit
    let parallel_threads = get_parallel_threads(cloud_str)?;

    // Return minimum of FD-based and thread-based limits, at least 1
    let workers = max_workers_fd.min(parallel_threads).max(1);

    Ok(workers)
}

/// Get file descriptor limits (soft and hard).
///
/// # Returns
/// Tuple of (soft_limit, hard_limit)
fn get_fd_limits() -> Result<(u64, u64), SkyPilotError> {
    #[cfg(unix)]
    {
        let limits = nix::sys::resource::getrlimit(nix::sys::resource::Resource::RLIMIT_NOFILE)
            .map_err(|e| SkyPilotError::SystemError(format!("Failed to get FD limits: {}", e)))?;

        Ok((limits.0, limits.1))
    }

    #[cfg(not(unix))]
    {
        // Default conservative limits for non-Unix
        Ok((1024, 4096))
    }
}

/// Get current file descriptor count for a directory.
///
/// Estimates the number of file descriptors needed for a directory.
/// This is a simplified version that counts entries.
///
/// # Arguments
/// * `path` - Directory path to analyze
///
/// # Returns
/// Estimated number of file descriptors needed
#[pyfunction]
#[pyo3(signature = (path))]
pub fn estimate_fd_for_directory(path: &str) -> PyResult<usize> {
    match fs::read_dir(path) {
        Ok(entries) => {
            let count = entries.count();
            // Rough estimate: 5 FDs per entry on average
            Ok(count * 5)
        }
        Err(e) => Err(SkyPilotError::IoError(e).into()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_get_parallel_threads() {
        // Default case
        let threads = get_parallel_threads(None).unwrap();
        assert!(threads >= 4);

        // Kubernetes case
        let k8s_threads = get_parallel_threads(Some("kubernetes")).unwrap();
        assert!(k8s_threads >= threads);
        assert_eq!(k8s_threads, threads * 4);

        // Case insensitive
        let k8s_lower = get_parallel_threads(Some("KUBERNETES")).unwrap();
        assert_eq!(k8s_lower, k8s_threads);
    }

    #[test]
    fn test_is_process_alive() {
        // Current process should be alive
        let current_pid = std::process::id() as i32;
        assert!(is_process_alive(current_pid).unwrap());

        // PID 1 should always exist (init/systemd)
        #[cfg(unix)]
        assert!(is_process_alive(1).unwrap());

        // Very high PID unlikely to exist
        assert!(!is_process_alive(9999999).unwrap());
    }

    #[test]
    fn test_get_max_workers() {
        let workers = get_max_workers_for_file_mounts(10, 100, None).unwrap();
        assert!(workers >= 1);
        assert!(workers <= get_parallel_threads(None).unwrap());
    }

    #[test]
    fn test_get_fd_limits() {
        let (soft, hard) = get_fd_limits().unwrap();
        assert!(soft > 0);
        assert!(hard >= soft);
    }

    #[test]
    fn test_estimate_fd_for_directory() {
        // Test with /tmp which should exist
        let result = estimate_fd_for_directory("/tmp");
        assert!(result.is_ok());
    }
}
