//! System monitoring

use sysinfo::{System, SystemExt, CpuExt};

/// System monitor
pub struct SystemMonitor {
    system: System,
}

impl SystemMonitor {
    /// Create new system monitor
    pub fn new() -> Self {
        let mut system = System::new_all();
        system.refresh_all();
        
        Self { system }
    }

    /// Update system metrics
    pub fn update(&mut self) {
        self.system.refresh_all();
    }

    /// Get CPU count
    pub fn cpu_count(&self) -> usize {
        self.system.cpus().len()
    }

    /// Get total memory in GB
    pub fn total_memory_gb(&self) -> f64 {
        self.system.total_memory() as f64 / 1024.0 / 1024.0 / 1024.0
    }

    /// Get used memory in GB
    pub fn used_memory_gb(&self) -> f64 {
        self.system.used_memory() as f64 / 1024.0 / 1024.0 / 1024.0
    }

    /// Get CPU usage percentage
    pub fn cpu_usage(&self) -> f32 {
        self.system.global_cpu_usage()
    }
}

impl Default for SystemMonitor {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_system_monitor() {
        let monitor = SystemMonitor::new();
        assert!(monitor.cpu_count() > 0);
        assert!(monitor.total_memory_gb() > 0.0);
    }
}
