# Rust-Migration f?r SkyPilot - Entwickler-Guidelines

## ?berblick

Dieses Dokument beschreibt den Prozess und die Best Practices f?r die schrittweise Migration performancekritischer Python-Utilities nach Rust unter Verwendung von PyO3.

## Ziele

- **Performance**: 2-10x Geschwindigkeitsverbesserungen f?r kritische Funktionen
- **Speichereffizienz**: Reduzierter Memory-Footprint durch Rust's Zero-Cost Abstractions
- **Zuverl?ssigkeit**: Memory-Safety und Thread-Safety garantiert
- **Nahtlose Integration**: Transparent f?r Endnutzer mit Python-Fallback

## Architektur

```
workspace/
??? rust/                      # Rust-Workspace
?   ??? Cargo.toml            # Workspace-Konfiguration
?   ??? rustfmt.toml          # Formatierungs-Regeln
?   ??? skypilot-utils/       # Haupt-Crate
?       ??? src/
?       ?   ??? lib.rs        # PyO3-Modul Definition
?       ?   ??? errors.rs     # Fehlerbehandlung
?       ?   ??? io_utils.rs   # I/O-Funktionen
?       ?   ??? string_utils.rs
?       ?   ??? system_utils.rs
?       ??? benches/          # Criterion-Benchmarks
?       ??? tests/            # Integrationstests
??? sky/
    ??? utils/
        ??? rust_fallback.py  # Python-Wrapper mit Fallback
```

## Entwicklungs-Setup

### Voraussetzungen

```bash
# Rust installieren (falls nicht vorhanden)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Maturin installieren (PyO3 Build-Tool)
pip install maturin

# Development-Dependencies
pip install pytest pytest-benchmark
```

### Lokales Entwickeln

```bash
# Rust-Code kompilieren und als Python-Modul installieren
cd rust/skypilot-utils
maturin develop  # Debug-Build

# Oder f?r Performance-Tests
maturin develop --release

# Rust-Code formatieren
cd rust
cargo fmt --all

# Linter ausf?hren
cargo clippy --all-targets --all-features

# Benchmarks ausf?hren
cargo bench
```

## Migration Workflow

### 1. Funktion identifizieren

Kriterien f?r Migration nach Rust:
- ? CPU-intensive Operationen (Parsing, Encoding, Hashing)
- ? I/O-heavy Operations (Datei-Operationen, Netzwerk)
- ? H?ufig aufgerufen (Hot Path)
- ? Wenig Python-spezifische Features (z.B. kein `eval`, `exec`)
- ? Starke Abh?ngigkeiten von Python-Objektmodell
- ? Dynamisches Verhalten (z.B. Metaprogrammierung)

### 2. Rust-Implementierung

**Beispiel: `read_last_n_lines`**

```rust
// rust/skypilot-utils/src/io_utils.rs
use pyo3::prelude::*;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};

#[pyfunction]
#[pyo3(signature = (file_path, n_lines))]
pub fn read_last_n_lines(file_path: &str, n_lines: usize) -> PyResult<Vec<String>> {
    // Implementation...
    Ok(result)
}

// Tests hinzuf?gen
#[cfg(test)]
mod tests {
    use super::*;
    
    #[test]
    fn test_read_last_n_lines() {
        // Test-Code
    }
}
```

### 3. PyO3-Binding registrieren

```rust
// rust/skypilot-utils/src/lib.rs
#[pymodule]
fn sky_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(io_utils::read_last_n_lines, m)?)?;
    Ok(())
}
```

### 4. Python-Wrapper mit Fallback

```python
# sky/utils/rust_fallback.py
import os

USE_RUST = os.environ.get('SKYPILOT_USE_RUST', '1') == '1'

try:
    import sky_rs
    _RUST_AVAILABLE = USE_RUST
except ImportError:
    _RUST_AVAILABLE = False

def read_last_n_lines(file_path: str, n_lines: int) -> List[str]:
    """Read last N lines from file (Rust-accelerated)."""
    if _RUST_AVAILABLE:
        try:
            return sky_rs.read_last_n_lines(file_path, n_lines)
        except Exception as e:
            logger.warning(f"Rust call failed, using Python fallback: {e}")
    
    # Python-Fallback
    return _python_read_last_n_lines(file_path, n_lines)

def _python_read_last_n_lines(file_path: str, n_lines: int) -> List[str]:
    """Original Python implementation."""
    # Bestehende Implementierung...
    pass
```

### 5. Integrieren in bestehendes Modul

```python
# sky/utils/common_utils.py
from sky.utils.rust_fallback import read_last_n_lines

# Bestehender Code nutzt automatisch Rust-Version
```

### 6. Tests schreiben

```python
# tests/unit_tests/test_rust_utils.py
import pytest
import sky_rs

def test_read_last_n_lines_rust():
    """Test Rust implementation."""
    # Test-Code
    
def test_read_last_n_lines_equivalence():
    """Ensure Rust and Python versions produce same results."""
    # Vergleich beider Implementierungen
```

### 7. Benchmarks

```python
# tests/benchmarks/bench_io_utils.py
import pytest

@pytest.mark.benchmark(group="read_lines")
def test_read_last_n_lines_rust(benchmark):
    benchmark(sky_rs.read_last_n_lines, test_file, 100)

@pytest.mark.benchmark(group="read_lines")
def test_read_last_n_lines_python(benchmark):
    benchmark(python_read_last_n_lines, test_file, 100)
```

## Best Practices

### Fehlerbehandlung

```rust
// Konsistente Error-Typen verwenden
use crate::errors::SkyPilotError;

pub fn risky_operation() -> Result<T, SkyPilotError> {
    let file = File::open(path)
        .map_err(|e| SkyPilotError::IoError(e))?;
    // ...
}

// Automatische Konvertierung zu Python-Exceptions
impl From<SkyPilotError> for PyErr {
    fn from(err: SkyPilotError) -> Self {
        PyException::new_err(err.to_string())
    }
}
```

### Logging

```rust
use log::{info, warn, error, debug};

pub fn some_function() -> PyResult<()> {
    debug!("Starting operation with params: {:?}", params);
    
    match operation() {
        Ok(result) => {
            info!("Operation succeeded");
            Ok(result)
        }
        Err(e) => {
            error!("Operation failed: {}", e);
            Err(e.into())
        }
    }
}
```

### Performance-Optimierung

```rust
// Nutze Rust-spezifische Optimierungen
#[inline]
pub fn hot_path_function() {
    // Frequently called code
}

// Vermeide unn?tige Allokationen
pub fn process_data(data: &[u8]) -> &[u8] {
    // Work with slices instead of Vec when possible
}

// Parallele Verarbeitung
use rayon::prelude::*;
data.par_iter().map(|x| process(x)).collect()
```

## Feature-Flags

```python
# Environment-Variable zum Deaktivieren von Rust
export SKYPILOT_USE_RUST=0  # Python-Fallback erzwingen

# In Code
import os
USE_RUST = os.environ.get('SKYPILOT_USE_RUST', '1') == '1'
```

## CI/CD Integration

### GitHub Actions Workflow

Siehe `.github/workflows/rust-ci.yml` f?r vollst?ndige CI-Konfiguration:

- ? Format-Checks (`cargo fmt`)
- ? Linting (`cargo clippy`)
- ? Multi-Platform Builds (Linux, macOS)
- ? Multi-Python Versions (3.8-3.12)
- ? Integration Tests
- ? Benchmarks
- ? Security Audits

### Lokale Pre-Commit Checks

```bash
# Format und Lint vor Commit
cd rust
cargo fmt --all
cargo clippy --all-targets --all-features
cargo test --lib
```

## Release-Prozess

### 1. Version Bump

```toml
# rust/Cargo.toml
[workspace.package]
version = "0.2.0"  # Bump version
```

### 2. Changelog

```markdown
# Changelog

## [0.2.0] - 2024-10-31
### Added
- Rust implementation of `read_last_n_lines` (5x faster)
- Rust implementation of `hash_file` with multi-algorithm support

### Performance
- File reading: 5x speedup on large files
- MD5 hashing: 3x speedup
```

### 3. Build Wheels

```bash
cd rust/skypilot-utils

# Lokales Wheel
maturin build --release

# F?r manylinux (Cross-Platform)
docker run --rm -v $(pwd):/io konstin2/maturin build --release --manylinux 2014
```

### 4. Release Notes

Immer Rust-?nderungen in Release-Notes hervorheben:
```markdown
## ? Performance Improvements (Rust)
- Migration von I/O-Utilities nach Rust: 2-5x Geschwindigkeitsverbesserung
- Reduzierter Speicherverbrauch bei gro?en Datei-Operationen
```

## Troubleshooting

### Problem: Import-Fehler

```python
ImportError: No module named 'sky_rs'
```

**L?sung:**
```bash
cd rust/skypilot-utils
maturin develop
```

### Problem: Linking-Fehler

```
undefined reference to `Py_*`
```

**L?sung:** PyO3 ben?tigt Python zur Laufzeit. Tests funktionieren nur im Python-Kontext.

### Problem: Performance schlechter als erwartet

**Checkliste:**
- [ ] Release-Build verwenden (`maturin develop --release`)
- [ ] Profiling durchf?hren (`cargo flamegraph`)
- [ ] Unn?tige Kopien vermeiden
- [ ] Parallelisierung erw?gen (rayon)

## Metriken und Monitoring

### Performance-Tracking

```python
# Optional: Telemetrie f?r Rust-Funktionen
import time
from sky.utils import metrics

def read_last_n_lines_instrumented(file_path, n_lines):
    start = time.perf_counter()
    result = sky_rs.read_last_n_lines(file_path, n_lines)
    duration = time.perf_counter() - start
    
    metrics.record_histogram('rust.read_last_n_lines.duration', duration)
    return result
```

### Benchmark-Baselines

Benchmarks werden in CI gespeichert und k?nnen verglichen werden:
```bash
# Baseline erstellen
cargo bench -- --save-baseline main

# Mit Baseline vergleichen
cargo bench -- --baseline main
```

## Roadmap

### Phase 1 (Aktuell) ?
- [x] Rust-Workspace Setup
- [x] CI/CD Integration
- [x] I/O Utilities (read_last_n_lines, hash_file, find_free_port)
- [x] String Utilities (base36_encode, format_float, truncate_long_string)
- [x] System Utilities (get_cpu_count, get_mem_size_gb)

### Phase 2 (Q1 2025)
- [ ] `subprocess_utils` Migration
- [ ] `perf_utils` Migration  
- [ ] Async I/O Support
- [ ] Windows Support

### Phase 3 (Q2 2025)
- [ ] Cloud Provider API Wrappers
- [ ] Advanced Telemetrie
- [ ] Custom Allocator (jemalloc)

## Weitere Ressourcen

- [PyO3 User Guide](https://pyo3.rs/)
- [Rust Book](https://doc.rust-lang.org/book/)
- [Criterion Benchmarking](https://bheisler.github.io/criterion.rs/book/)
- [maturin Docs](https://www.maturin.rs/)

## Kontakt & Support

- **Fragen**: Erstellen Sie ein Issue mit Label `rust-migration`
- **Diskussionen**: GitHub Discussions
- **Dokumentation**: Siehe `rust/skypilot-utils/README.md`

---

**Letzte Aktualisierung**: 2024-10-31  
**Autor**: SkyPilot Team
