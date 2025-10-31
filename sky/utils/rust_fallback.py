"""
Rust-accelerated utilities with Python fallback.

This module provides a unified interface for performance-critical utilities
that can optionally use Rust implementations via PyO3. Falls back gracefully
to pure Python if Rust extensions are not available.

Environment Variables:
    SKYPILOT_USE_RUST: Set to '0' to force Python fallback (default: '1')
"""

import hashlib
import os
import socket
from typing import List, Union

from sky import sky_logging

logger = sky_logging.init_logger(__name__)

# Check if Rust extensions are available and enabled
USE_RUST = os.environ.get('SKYPILOT_USE_RUST', '1') == '1'

try:
    if USE_RUST:
        import sky_rs
        _RUST_AVAILABLE = True
        logger.debug(f'Rust utilities loaded (version {sky_rs.__version__})')
    else:
        _RUST_AVAILABLE = False
        logger.debug('Rust utilities disabled via environment variable')
except ImportError:
    _RUST_AVAILABLE = False
    logger.debug('Rust utilities not available, using Python fallback')


# ============================================================================
# I/O Utilities
# ============================================================================


def read_last_n_lines(file_path: str,
                      n: int,
                      chunk_size: int = 8192,
                      encoding: str = 'utf-8',
                      errors: str = 'replace') -> List[str]:
    """Read the last N lines of a file.

    Uses Rust implementation for better performance if available.

    Args:
        file_path: Path to the file to read.
        n: Number of lines to read from the end of the file.
        chunk_size: Size of chunks in bytes (Python fallback only).
        encoding: Encoding to use when decoding binary chunks.
        errors: Error handling for decode errors.

    Returns:
        A list of the last N lines.
    """
    if _RUST_AVAILABLE:
        try:
            # Rust implementation
            lines = sky_rs.read_last_n_lines(file_path, n)
            return lines
        except Exception as e:
            logger.warning(f'Rust read_last_n_lines failed: {e}, using Python fallback')

    # Python fallback
    return _python_read_last_n_lines(file_path, n, chunk_size, encoding, errors)


def _python_read_last_n_lines(file_path: str,
                               n: int,
                               chunk_size: int = 8192,
                               encoding: str = 'utf-8',
                               errors: str = 'replace') -> List[str]:
    """Python implementation of read_last_n_lines."""
    assert n >= 0, f'n must be non-negative. Got {n}'
    assert chunk_size > 0, f'chunk_size must be positive. Got {chunk_size}'
    assert os.path.exists(file_path), f'File not found: {file_path}'

    if n == 0:
        return []

    with open(file_path, 'rb') as f:
        f.seek(0, os.SEEK_END)
        file_size = f.tell()
        if file_size == 0:
            return []

        pos = file_size
        lines_found = 0
        chunks = []

        while pos > 0 and lines_found <= n:
            read_size = min(chunk_size, pos)
            pos -= read_size
            f.seek(pos)
            chunk = f.read(read_size)
            chunks.append(chunk)
            lines_found += chunk.count(b'\n')

        full_bytes = b''.join(reversed(chunks))
        content = full_bytes.decode(encoding, errors=errors)
        lines = content.splitlines()

        return lines[-n:] if len(lines) > n else lines


def hash_file(file_path: str, algorithm: str = 'sha256') -> 'hashlib._Hash':
    """Compute hash of a file.

    Uses Rust implementation for better performance if available.

    Args:
        file_path: Path to the file to hash.
        algorithm: Hash algorithm ('md5', 'sha256', or 'sha512').

    Returns:
        Hash object.
    """
    if _RUST_AVAILABLE:
        try:
            # Rust implementation returns hex string
            hex_digest = sky_rs.hash_file(file_path, algorithm)
            # Convert to hash object for compatibility
            hasher = hashlib.new(algorithm)
            hasher._hex_digest = hex_digest
            return hasher
        except Exception as e:
            logger.warning(f'Rust hash_file failed: {e}, using Python fallback')

    # Python fallback
    return _python_hash_file(file_path, algorithm)


def _python_hash_file(file_path: str, algorithm: str) -> 'hashlib._Hash':
    """Python implementation of hash_file."""
    hasher = hashlib.new(algorithm)
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            hasher.update(chunk)
    return hasher


def find_free_port(start_port: int) -> int:
    """Find a free TCP port starting from the given port.

    Uses Rust implementation for better performance if available.

    Args:
        start_port: Port number to start searching from.

    Returns:
        First available port number.
    """
    if _RUST_AVAILABLE:
        try:
            return sky_rs.find_free_port(start_port)
        except Exception as e:
            logger.warning(f'Rust find_free_port failed: {e}, using Python fallback')

    # Python fallback
    return _python_find_free_port(start_port)


def _python_find_free_port(start_port: int) -> int:
    """Python implementation of find_free_port."""
    for port in range(start_port, 65535):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('127.0.0.1', port))
                return port
        except OSError:
            continue
    raise RuntimeError('No free port available')


# ============================================================================
# String Utilities
# ============================================================================


def base36_encode(hex_str: str) -> str:
    """Convert a hexadecimal string to base36.

    Uses Rust implementation for better performance if available.

    Args:
        hex_str: Hexadecimal string to encode.

    Returns:
        Base36 encoded string.
    """
    if _RUST_AVAILABLE:
        try:
            return sky_rs.base36_encode(hex_str)
        except Exception as e:
            logger.warning(f'Rust base36_encode failed: {e}, using Python fallback')

    # Python fallback
    return _python_base36_encode(hex_str)


def _python_base36_encode(hex_str: str) -> str:
    """Python implementation of base36_encode."""
    int_value = int(hex_str, 16)

    if int_value == 0:
        return '0'

    alphabet = '0123456789abcdefghijklmnopqrstuvwxyz'
    base36 = ''
    while int_value != 0:
        int_value, i = divmod(int_value, 36)
        base36 = alphabet[i] + base36
    return base36


def format_float(num: Union[float, int], precision: int = 1) -> str:
    """Format a float with specified precision.

    Uses Rust implementation for better performance if available.

    Args:
        num: Number to format.
        precision: Number of decimal places.

    Returns:
        Formatted string representation.
    """
    if _RUST_AVAILABLE:
        try:
            return sky_rs.format_float(float(num), precision)
        except Exception as e:
            logger.warning(f'Rust format_float failed: {e}, using Python fallback')

    # Python fallback
    return _python_format_float(num, precision)


def _python_format_float(num: Union[float, int], precision: int = 1) -> str:
    """Python implementation of format_float."""
    if abs(num) >= 1e12:
        return f'{num / 1e12:.{precision}f}T'
    elif abs(num) >= 1e9:
        return f'{num / 1e9:.{precision}f}B'
    elif abs(num) >= 1e6:
        return f'{num / 1e6:.{precision}f}M'
    elif abs(num) >= 1e3:
        return f'{num / 1e3:.{precision}f}K'
    else:
        return f'{num:.{precision}f}'


def truncate_long_string(s: str,
                         max_length: int = 80,
                         placeholder: str = '...') -> str:
    """Truncate a string to a maximum length.

    Uses Rust implementation for better performance if available.

    Args:
        s: String to truncate.
        max_length: Maximum length.
        placeholder: Placeholder for truncated part.

    Returns:
        Truncated string.
    """
    if _RUST_AVAILABLE:
        try:
            return sky_rs.truncate_long_string(s, max_length, placeholder)
        except Exception as e:
            logger.warning(f'Rust truncate_long_string failed: {e}, using Python fallback')

    # Python fallback
    return _python_truncate_long_string(s, max_length, placeholder)


def _python_truncate_long_string(s: str,
                                  max_length: int = 80,
                                  placeholder: str = '...') -> str:
    """Python implementation of truncate_long_string."""
    if len(s) <= max_length:
        return s

    placeholder_len = len(placeholder)
    if max_length <= placeholder_len:
        return placeholder[:max_length]

    keep_len = max_length - placeholder_len
    return s[:keep_len] + placeholder


# ============================================================================
# System Utilities
# ============================================================================


def get_cpu_count() -> int:
    """Get the number of available CPU cores.

    Uses Rust implementation (cgroup-aware) if available.

    Returns:
        Number of CPU cores available.
    """
    if _RUST_AVAILABLE:
        try:
            return sky_rs.get_cpu_count()
        except Exception as e:
            logger.warning(f'Rust get_cpu_count failed: {e}, using Python fallback')

    # Python fallback
    return _python_get_cpu_count()


def _python_get_cpu_count() -> int:
    """Python implementation of get_cpu_count."""
    import multiprocessing
    return multiprocessing.cpu_count()


def get_mem_size_gb() -> float:
    """Get total system memory in gigabytes.

    Uses Rust implementation for better performance if available.

    Returns:
        Total memory size in GB.
    """
    if _RUST_AVAILABLE:
        try:
            return sky_rs.get_mem_size_gb()
        except Exception as e:
            logger.warning(f'Rust get_mem_size_gb failed: {e}, using Python fallback')

    # Python fallback
    return _python_get_mem_size_gb()


def _python_get_mem_size_gb() -> float:
    """Python implementation of get_mem_size_gb."""
    try:
        import psutil
        return psutil.virtual_memory().total / (1024**3)
    except ImportError:
        # Fallback if psutil not available
        with open('/proc/meminfo', 'r') as f:
            for line in f:
                if line.startswith('MemTotal:'):
                    kb = int(line.split()[1])
                    return kb / (1024**2)
        return 0.0


# ============================================================================
# Utilities
# ============================================================================


def is_rust_available() -> bool:
    """Check if Rust extensions are available and enabled.

    Returns:
        True if Rust extensions can be used, False otherwise.
    """
    return _RUST_AVAILABLE


def get_backend_info() -> dict:
    """Get information about the current backend (Rust or Python).

    Returns:
        Dictionary with backend information.
    """
    if _RUST_AVAILABLE:
        return {
            'backend': 'rust',
            'version': sky_rs.__version__,
            'available': True,
        }
    else:
        return {
            'backend': 'python',
            'version': None,
            'available': False,
        }
