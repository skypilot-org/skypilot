//! System utility functions
//!
//! System information and resource queries:
//! - CPU count (with cgroup awareness)
//! - Memory size detection

use pyo3::prelude::*;
use std::fs;
use sysinfo::System;

use crate::errors::SkyPilotError;

/// Get the number of available CPU cores.
///
/// This function is cgroup-aware and will return the correct value
/// in containerized environments.
///
/// # Returns
/// Number of CPU cores available
#[pyfunction]
pub fn get_cpu_count() -> PyResult<usize> {
    // First, try to read from cgroup v2
    if let Ok(count) = read_cgroup_v2_cpu_quota() {
        return Ok(count);
    }

    // Then try cgroup v1
    if let Ok(count) = read_cgroup_v1_cpu_quota() {
        return Ok(count);
    }

    // Fallback to system CPU count
    Ok(num_cpus::get())
}

/// Get total system memory in gigabytes.
///
/// # Returns
/// Total memory size in GB
#[pyfunction]
pub fn get_mem_size_gb() -> PyResult<f64> {
    let mut sys = System::new_all();
    sys.refresh_memory();

    let total_bytes = sys.total_memory();
    let gb = total_bytes as f64 / (1024.0 * 1024.0 * 1024.0);

    Ok(gb)
}

// Helper functions for cgroup CPU detection

fn read_cgroup_v2_cpu_quota() -> Result<usize, SkyPilotError> {
    // cgroup v2: /sys/fs/cgroup/cpu.max contains "max period" or "quota period"
    let cpu_max_path = "/sys/fs/cgroup/cpu.max";

    if let Ok(content) = fs::read_to_string(cpu_max_path) {
        let parts: Vec<&str> = content.split_whitespace().collect();
        if parts.len() == 2 {
            if parts[0] == "max" {
                // No quota set, use physical CPUs
                return Err(SkyPilotError::NotFound("No cgroup v2 quota".to_string()));
            }

            let quota: i64 = parts[0]
                .parse()
                .map_err(|_| SkyPilotError::ParseError("Invalid quota".to_string()))?;
            let period: i64 = parts[1]
                .parse()
                .map_err(|_| SkyPilotError::ParseError("Invalid period".to_string()))?;

            if quota > 0 && period > 0 {
                let cpus = ((quota as f64) / (period as f64)).ceil() as usize;
                return Ok(cpus.max(1));
            }
        }
    }

    Err(SkyPilotError::NotFound("cgroup v2 not found".to_string()))
}

fn read_cgroup_v1_cpu_quota() -> Result<usize, SkyPilotError> {
    // cgroup v1: read quota and period separately
    let quota_path = "/sys/fs/cgroup/cpu/cpu.cfs_quota_us";
    let period_path = "/sys/fs/cgroup/cpu/cpu.cfs_period_us";

    let quota = fs::read_to_string(quota_path)
        .ok()
        .and_then(|s| s.trim().parse::<i64>().ok());

    let period = fs::read_to_string(period_path)
        .ok()
        .and_then(|s| s.trim().parse::<i64>().ok());

    if let (Some(q), Some(p)) = (quota, period) {
        if q > 0 && p > 0 {
            let cpus = ((q as f64) / (p as f64)).ceil() as usize;
            return Ok(cpus.max(1));
        }
    }

    Err(SkyPilotError::NotFound("cgroup v1 not found".to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_get_cpu_count() {
        let count = get_cpu_count().unwrap();
        assert!(count > 0);
        assert!(count <= 1024); // Sanity check
    }

    #[test]
    fn test_get_mem_size_gb() {
        let mem = get_mem_size_gb().unwrap();
        assert!(mem > 0.0);
        assert!(mem < 100000.0); // Sanity check: less than 100TB
    }
}
