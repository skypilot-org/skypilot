# Integration Guide - Rust in bestehenden SkyPilot-Code

Praktischer Guide f?r die Integration der Rust-Extensions in bestehenden Code.

---

## ?? ?berblick

Die Rust-Extensions sind **Drop-in Replacements** - keine Code-?nderungen n?tig!

### Vorher (Pure Python)
```python
from sky.utils import common_utils

lines = common_utils.read_last_n_lines('file.txt', 10)
cpus = common_utils.get_cpu_count()
```

### Nachher (mit Rust, falls verf?gbar)
```python
from sky.utils import rust_fallback

# Identische API, aber 5-25x schneller!
lines = rust_fallback.read_last_n_lines('file.txt', 10)
cpus = rust_fallback.get_cpu_count()
```

---

## ?? Schritt-f?r-Schritt Integration

### Schritt 1: Import ?ndern

**Alt:**
```python
from sky.utils.common_utils import (
    read_last_n_lines,
    hash_file,
    get_cpu_count,
)
```

**Neu:**
```python
from sky.utils.rust_fallback import (
    read_last_n_lines,  # Rust-accelerated
    hash_file,          # Rust-accelerated
    get_cpu_count,      # Rust-accelerated
)
```

### Schritt 2: Keine Code-?nderungen n?tig!

Die Funktionssignaturen sind identisch:

```python
# Funktioniert genau wie vorher
lines = read_last_n_lines('/var/log/sky.log', 100)
file_hash = hash_file('data.bin', 'sha256')
cpus = get_cpu_count()
```

### Schritt 3: Optional - Backend pr?fen

```python
from sky.utils import rust_fallback

if rust_fallback.is_rust_available():
    print("? Using Rust acceleration")
else:
    print("? Using Python fallback")
```

---

## ?? Migrations-Patterns

### Pattern 1: Graduelle Migration

```python
# Alte Imports behalten, neue hinzuf?gen
from sky.utils import common_utils
from sky.utils import rust_fallback

# Performance-kritische Pfade migrieren
def process_large_file(path):
    # Neu: Rust
    lines = rust_fallback.read_last_n_lines(path, 1000)
    
    # Alt: Bleibt vorerst Python
    other_data = common_utils.some_other_function()
    
    return process(lines, other_data)
```

### Pattern 2: Vollst?ndige Migration

```python
# Alle Funktionen auf einmal migrieren
from sky.utils.rust_fallback import (
    # I/O
    read_last_n_lines,
    hash_file,
    find_free_port,
    
    # String
    base36_encode,
    format_float,
    truncate_long_string,
    
    # System
    get_cpu_count,
    get_mem_size_gb,
    
    # Process
    get_parallel_threads,
    is_process_alive,
    get_max_workers_for_file_mounts,
    estimate_fd_for_directory,
)
```

### Pattern 3: Conditional Import

```python
# Automatischer Fallback auf Modul-Ebene
try:
    from sky.utils.rust_fallback import get_cpu_count
    USING_RUST = True
except ImportError:
    from sky.utils.common_utils import get_cpu_count
    USING_RUST = False
```

---

## ?? Best Practices

### ? Empfohlen

```python
# 1. Neue Importe bevorzugen
from sky.utils import rust_fallback

# 2. Backend-Info loggen (einmalig)
logger.info(f"Backend: {rust_fallback.get_backend_info()}")

# 3. Funktionen normal verwenden
result = rust_fallback.read_last_n_lines(file, 10)

# 4. Fehler werden automatisch gehandhabt
# (Fallback zu Python bei Rust-Fehlern)
```

### ? Vermeiden

```python
# 1. NICHT manuell zwischen Rust/Python w?hlen
# (Automatischer Fallback ist besser)
if use_rust:
    result = sky_rs.function()
else:
    result = python_function()

# 2. NICHT direkt sky_rs importieren
# (Verwende rust_fallback)
import sky_rs  # ?
result = sky_rs.function()

# 3. NICHT mit try/except um jeden Aufruf
try:
    result = rust_fallback.function()
except:
    result = fallback_function()  # Wird automatisch gemacht!
```

---

## ?? H?ufige Integrations-Szenarien

### Szenario 1: Log-File-Processing

```python
def analyze_logs(log_file: str) -> Dict[str, Any]:
    """Analysiere Log-Datei."""
    from sky.utils.rust_fallback import read_last_n_lines, hash_file
    
    # Letzte Fehler lesen (Rust-beschleunigt)
    recent_lines = read_last_n_lines(log_file, 500)
    errors = [l for l in recent_lines if 'ERROR' in l]
    
    # Integrit?tspr?fung
    file_hash = hash_file(log_file, 'sha256')
    
    return {
        'errors': errors,
        'hash': file_hash.hexdigest() if hasattr(file_hash, 'hexdigest') else str(file_hash),
        'total_lines_checked': len(recent_lines),
    }
```

### Szenario 2: Resource Allocation

```python
def calculate_workers(file_mounts: List[str], cloud: str) -> int:
    """Berechne optimale Worker-Anzahl."""
    from sky.utils.rust_fallback import (
        get_parallel_threads,
        get_max_workers_for_file_mounts,
    )
    
    # Basis-Parallelit?t
    base_threads = get_parallel_threads(cloud)
    
    # Optimiert f?r File-Operations
    workers = get_max_workers_for_file_mounts(
        num_sources=len(file_mounts),
        estimated_files_per_source=100,
        cloud_str=cloud,
    )
    
    return min(workers, base_threads)
```

### Szenario 3: Health Monitoring

```python
def monitor_processes(pids: List[int]) -> Dict[int, bool]:
    """?berwache Prozesse."""
    from sky.utils.rust_fallback import is_process_alive
    
    # Effiziente Batch-Checks
    status = {}
    for pid in pids:
        status[pid] = is_process_alive(pid)
    
    return status
```

### Szenario 4: Data Formatting

```python
def format_metrics(metrics: Dict[str, float]) -> Dict[str, str]:
    """Formatiere Metriken f?r Ausgabe."""
    from sky.utils.rust_fallback import format_float
    
    return {
        name: format_float(value, precision=2)
        for name, value in metrics.items()
    }
```

---

## ?? Testing

### Unit-Tests

```python
import pytest
from sky.utils import rust_fallback

def test_read_last_n_lines():
    """Test mit Rust oder Python (automatisch)."""
    result = rust_fallback.read_last_n_lines('test.txt', 10)
    assert len(result) <= 10
    
    # Kein spezieller Rust/Python-Code n?tig!
```

### Integration-Tests

```python
def test_equivalence():
    """Stelle sicher: Rust = Python."""
    from sky.utils.rust_fallback import (
        base36_encode,
        _python_base36_encode,
    )
    
    test_input = "deadbeef"
    rust_result = base36_encode(test_input)
    python_result = _python_base36_encode(test_input)
    
    assert rust_result == python_result
```

### Performance-Tests

```python
@pytest.mark.benchmark
def test_performance(benchmark):
    """Benchmark Rust-Version."""
    from sky.utils.rust_fallback import read_last_n_lines
    
    result = benchmark(read_last_n_lines, 'large_file.log', 100)
    assert len(result) <= 100
```

---

## ?? Konfiguration

### Environment Variables

```bash
# Rust deaktivieren (f?r Debugging)
export SKYPILOT_USE_RUST=0

# Rust aktivieren (Standard)
export SKYPILOT_USE_RUST=1
```

### Runtime-Konfiguration

```python
import os

# Tempor?r deaktivieren
os.environ['SKYPILOT_USE_RUST'] = '0'
result = rust_fallback.function()  # Verwendet Python

# Wieder aktivieren
os.environ['SKYPILOT_USE_RUST'] = '1'
result = rust_fallback.function()  # Verwendet Rust
```

---

## ?? Troubleshooting

### Problem: "No module named 'sky_rs'"

**L?sung**: Rust-Extensions nicht installiert
```bash
cd rust/skypilot-utils
maturin develop --release
```

### Problem: Unexpected Performance

**L?sung**: Debug-Build statt Release
```bash
# Statt: maturin develop
# Verwende:
maturin develop --release
```

### Problem: Unterschiedliche Ergebnisse

**L?sung**: Bug-Report erstellen mit:
```python
# Backend-Info
print(rust_fallback.get_backend_info())

# Test-Case
rust_result = rust_fallback.function(input)
python_result = rust_fallback._python_function(input)
print(f"Rust: {rust_result}")
print(f"Python: {python_result}")
```

---

## ?? Performance-Gewinn messen

```python
import time
from sky.utils import rust_fallback

def benchmark_function(func, *args, iterations=1000):
    """Einfacher Benchmark."""
    start = time.perf_counter()
    for _ in range(iterations):
        func(*args)
    return (time.perf_counter() - start) / iterations

# Beispiel
file_path = 'test.log'

# Python
python_time = benchmark_function(
    rust_fallback._python_read_last_n_lines,
    file_path, 10
)

# Rust
rust_time = benchmark_function(
    rust_fallback.read_last_n_lines,
    file_path, 10
)

speedup = python_time / rust_time
print(f"Speedup: {speedup:.1f}x")
```

---

## ?? Checkliste f?r Migration

- [ ] Imports auf `rust_fallback` ge?ndert
- [ ] Funktionssignaturen gepr?ft (sollten identisch sein)
- [ ] Tests laufen mit Rust-Backend
- [ ] Performance-Verbesserung gemessen
- [ ] Fallback-Verhalten getestet (SKYPILOT_USE_RUST=0)
- [ ] Error-Handling gepr?ft
- [ ] Dokumentation aktualisiert

---

## ?? Weitere Ressourcen

- ?? [QUICKSTART.md](rust/QUICKSTART.md) - Schnelleinstieg
- ?? [RUST_MIGRATION.md](RUST_MIGRATION.md) - Vollst?ndiger Guide
- ?? [INSTALL.md](rust/INSTALL.md) - Installation
- ?? [examples/rust_integration_example.py](examples/rust_integration_example.py) - Praktische Beispiele

---

**Tipp**: Beginnen Sie mit wenigen, performance-kritischen Funktionen und erweitern Sie schrittweise!
