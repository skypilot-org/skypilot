//! SkyPilot Utilities - High-Performance Rust Implementations
//!
//! This crate provides Rust implementations of performance-critical utilities
//! for SkyPilot, exposed to Python via PyO3.

use pyo3::prelude::*;

pub mod errors;
pub mod io_utils;
pub mod process_utils;
pub mod string_utils;
pub mod system_utils;

/// Python module initialization
#[pymodule]
fn sky_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Initialize logger for Rust side
    env_logger::try_init().ok();

    // I/O utilities
    m.add_function(wrap_pyfunction!(io_utils::read_last_n_lines, m)?)?;
    m.add_function(wrap_pyfunction!(io_utils::hash_file, m)?)?;
    m.add_function(wrap_pyfunction!(io_utils::find_free_port, m)?)?;

    // String utilities
    m.add_function(wrap_pyfunction!(string_utils::base36_encode, m)?)?;
    m.add_function(wrap_pyfunction!(string_utils::format_float, m)?)?;
    m.add_function(wrap_pyfunction!(string_utils::truncate_long_string, m)?)?;

    // System utilities
    m.add_function(wrap_pyfunction!(system_utils::get_cpu_count, m)?)?;
    m.add_function(wrap_pyfunction!(system_utils::get_mem_size_gb, m)?)?;

    // Process utilities
    m.add_function(wrap_pyfunction!(process_utils::get_parallel_threads, m)?)?;
    m.add_function(wrap_pyfunction!(process_utils::is_process_alive, m)?)?;
    m.add_function(wrap_pyfunction!(
        process_utils::get_max_workers_for_file_mounts,
        m
    )?)?;
    m.add_function(wrap_pyfunction!(
        process_utils::estimate_fd_for_directory,
        m
    )?)?;

    // Module metadata
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add("__author__", "SkyPilot Team")?;

    Ok(())
}
