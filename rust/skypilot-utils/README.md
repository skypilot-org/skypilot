# SkyPilot Utils - Rust Implementation

High-performance Rust implementations of performance-critical utilities for SkyPilot, exposed to Python via PyO3.

## Features

### I/O Utilities
- **`read_last_n_lines`**: Efficiently read the last N lines from a file
- **`hash_file`**: Fast file hashing with MD5, SHA256, and SHA512
- **`find_free_port`**: Find available TCP ports

### String Utilities
- **`base36_encode`**: Convert hexadecimal strings to base36
- **`format_float`**: Intelligent float formatting with K/M/B/T suffixes
- **`truncate_long_string`**: String truncation with customizable ellipsis

### System Utilities
- **`get_cpu_count`**: cgroup-aware CPU count detection
- **`get_mem_size_gb`**: System memory detection in GB

## Building

### Development Build
```bash
cd rust
cargo build
```

### Release Build
```bash
cargo build --release
```

### Python Integration
```bash
# Install maturin
pip install maturin

# Build Python wheel
cd rust/skypilot-utils
maturin develop  # For development
maturin build --release  # For production
```

## Testing

### Rust Tests
```bash
cargo test
```

### Benchmarks
```bash
cargo bench
```

## Usage from Python

```python
# Import the Rust module
try:
    import sky_rs
    USE_RUST = True
except ImportError:
    USE_RUST = False

# Use with fallback
if USE_RUST:
    lines = sky_rs.read_last_n_lines("/path/to/file", 10)
else:
    # Python fallback implementation
    lines = python_read_last_n_lines("/path/to/file", 10)
```

## Performance Targets

Based on initial benchmarks:
- **`read_last_n_lines`**: 2-5x faster than Python
- **`hash_file`**: 3-7x faster than Python
- **`base36_encode`**: 5-10x faster than Python
- **`get_cpu_count`**: 10-20x faster with cgroup parsing

## Architecture

```
rust/
??? Cargo.toml           # Workspace configuration
??? skypilot-utils/      # Main crate
?   ??? Cargo.toml       # Crate configuration
?   ??? src/
?   ?   ??? lib.rs       # PyO3 module definition
?   ?   ??? errors.rs    # Error types
?   ?   ??? io_utils.rs  # I/O functions
?   ?   ??? string_utils.rs  # String operations
?   ?   ??? system_utils.rs  # System info
?   ??? benches/         # Criterion benchmarks
?   ??? tests/           # Integration tests
??? rustfmt.toml         # Code formatting rules
```

## Contributing

1. All Rust code must pass `cargo fmt` and `cargo clippy`
2. Add tests for new functionality
3. Run benchmarks to verify performance improvements
4. Update Python fallback implementations
5. Document breaking changes

## License

Apache 2.0 - See LICENSE file
