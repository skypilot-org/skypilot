#!/usr/bin/env python3
"""
Pr?fe die Rust-Installation und Performance.

Dieses Skript testet, ob die Rust-Extensions korrekt installiert sind
und f?hrt einfache Performance-Vergleiche durch.
"""

import os
import sys
import time
import tempfile
from typing import Callable

# Farben f?r Terminal-Output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
RESET = '\033[0m'
BOLD = '\033[1m'


def print_section(title: str):
    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}{title}{RESET}")
    print(f"{BOLD}{'='*60}{RESET}")


def print_success(msg: str):
    print(f"{GREEN}?{RESET} {msg}")


def print_error(msg: str):
    print(f"{RED}?{RESET} {msg}")


def print_warning(msg: str):
    print(f"{YELLOW}?{RESET} {msg}")


def benchmark(func: Callable, name: str, iterations: int = 1000):
    """F?hre einfachen Benchmark aus."""
    start = time.perf_counter()
    for _ in range(iterations):
        func()
    duration = time.perf_counter() - start
    return duration


def main():
    print(f"{BOLD}SkyPilot Rust Extensions - Installation Check{RESET}")
    print(f"Python: {sys.version}")
    
    # Check 1: Rust-Modul verf?gbar?
    print_section("1. Rust-Modul Verf?gbarkeit")
    
    try:
        import sky_rs
        print_success(f"sky_rs module loaded (version {sky_rs.__version__})")
        rust_available = True
    except ImportError as e:
        print_error(f"sky_rs module not found: {e}")
        print_warning("Run: cd rust/skypilot-utils && maturin develop")
        rust_available = False
    
    # Check 2: Fallback-Mechanismus
    print_section("2. Fallback-Mechanismus")
    
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
        from sky.utils import rust_fallback
        
        info = rust_fallback.get_backend_info()
        backend = info['backend']
        
        if backend == 'rust':
            print_success(f"Backend: Rust (version {info['version']})")
        else:
            print_warning("Backend: Python (Fallback)")
            
    except ImportError as e:
        print_error(f"Cannot import rust_fallback: {e}")
        return 1
    
    # Check 3: Funktionalit?ts-Tests
    print_section("3. Funktionalit?ts-Tests")
    
    test_results = []
    
    # Test read_last_n_lines
    try:
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            for i in range(100):
                f.write(f"Line {i}\n")
            fname = f.name
        
        try:
            lines = rust_fallback.read_last_n_lines(fname, 10)
            assert len(lines) == 10
            assert "Line 99" in lines[-1]
            print_success("read_last_n_lines: OK")
            test_results.append(True)
        finally:
            os.unlink(fname)
    except Exception as e:
        print_error(f"read_last_n_lines: {e}")
        test_results.append(False)
    
    # Test hash_file
    try:
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            f.write("test content")
            fname = f.name
        
        try:
            hash_obj = rust_fallback.hash_file(fname, "sha256")
            # Check if it's a valid hash
            assert hasattr(hash_obj, 'hexdigest') or hasattr(hash_obj, '_hex_digest')
            print_success("hash_file: OK")
            test_results.append(True)
        finally:
            os.unlink(fname)
    except Exception as e:
        print_error(f"hash_file: {e}")
        test_results.append(False)
    
    # Test find_free_port
    try:
        port = rust_fallback.find_free_port(10000)
        assert 10000 <= port < 65535
        print_success(f"find_free_port: OK (found port {port})")
        test_results.append(True)
    except Exception as e:
        print_error(f"find_free_port: {e}")
        test_results.append(False)
    
    # Test base36_encode
    try:
        result = rust_fallback.base36_encode("ff")
        assert isinstance(result, str) and len(result) > 0
        print_success(f"base36_encode: OK (ff -> {result})")
        test_results.append(True)
    except Exception as e:
        print_error(f"base36_encode: {e}")
        test_results.append(False)
    
    # Test format_float
    try:
        result = rust_fallback.format_float(1234567.89, 2)
        assert "M" in result or "m" in result.lower()
        print_success(f"format_float: OK (1234567.89 -> {result})")
        test_results.append(True)
    except Exception as e:
        print_error(f"format_float: {e}")
        test_results.append(False)
    
    # Test truncate_long_string
    try:
        long_str = "A" * 1000
        result = rust_fallback.truncate_long_string(long_str, 20, "...")
        assert len(result) == 20
        assert result.endswith("...")
        print_success("truncate_long_string: OK")
        test_results.append(True)
    except Exception as e:
        print_error(f"truncate_long_string: {e}")
        test_results.append(False)
    
    # Test get_cpu_count
    try:
        cpus = rust_fallback.get_cpu_count()
        assert cpus > 0 and cpus < 1024
        print_success(f"get_cpu_count: OK (detected {cpus} CPUs)")
        test_results.append(True)
    except Exception as e:
        print_error(f"get_cpu_count: {e}")
        test_results.append(False)
    
    # Test get_mem_size_gb
    try:
        mem = rust_fallback.get_mem_size_gb()
        assert mem > 0 and mem < 100000
        print_success(f"get_mem_size_gb: OK (detected {mem:.2f} GB)")
        test_results.append(True)
    except Exception as e:
        print_error(f"get_mem_size_gb: {e}")
        test_results.append(False)
    
    # Check 4: Performance-Vergleich (nur wenn Rust verf?gbar)
    if rust_available:
        print_section("4. Performance-Vergleich (1000 Iterationen)")
        
        # Base36 Encode Benchmark
        try:
            rust_time = benchmark(
                lambda: sky_rs.base36_encode("deadbeef"),
                "base36_encode (Rust)"
            )
            python_time = benchmark(
                lambda: rust_fallback._python_base36_encode("deadbeef"),
                "base36_encode (Python)"
            )
            speedup = python_time / rust_time
            print(f"  Rust:   {rust_time*1000:.2f} ms")
            print(f"  Python: {python_time*1000:.2f} ms")
            print_success(f"Speedup: {speedup:.2f}x")
        except Exception as e:
            print_warning(f"Benchmark failed: {e}")
    
    # Summary
    print_section("Zusammenfassung")
    
    total = len(test_results)
    passed = sum(test_results)
    
    if passed == total:
        print_success(f"Alle {total} Tests bestanden!")
        if rust_available:
            print_success("Rust-Extensions sind voll funktionsf?hig!")
        else:
            print_warning("Python-Fallback funktioniert korrekt")
        return 0
    else:
        print_error(f"{total - passed} von {total} Tests fehlgeschlagen")
        return 1


if __name__ == "__main__":
    sys.exit(main())
