# Contributing to SkyPilot Rust Extensions

Vielen Dank f?r Ihr Interesse an der Verbesserung der SkyPilot Rust-Extensions!

---

## ?? Arten von Beitr?gen

Wir freuen uns ?ber:

- ?? **Bug-Fixes**
- ? **Neue Funktionen** (nach Abstimmung)
- ?? **Dokumentations-Verbesserungen**
- ? **Performance-Optimierungen**
- ?? **Tests und Benchmarks**
- ?? **?bersetzungen**

---

## ?? Schnellstart f?r Entwickler

### Setup

```bash
# 1. Repository klonen
git clone https://github.com/skypilot-org/skypilot.git
cd skypilot

# 2. Rust-Toolchain
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
rustup component add rustfmt clippy

# 3. Development-Tools
cargo install cargo-watch cargo-audit
pip install maturin pytest pytest-benchmark

# 4. Build
cd rust
make dev
```

### Development-Zyklus

```bash
# Schneller Zyklus
make dev-cycle  # Format ? Lint ? Build ? Test

# Manuelle Schritte
make fmt        # Code formatieren
make lint       # Clippy ausf?hren
make dev        # Debug-Build
make test       # Tests laufen lassen
```

---

## ?? Code-Style

### Rust

Wir folgen Standard-Rust-Konventionen:

```rust
// ? Gut
pub fn my_function(param: &str) -> Result<String, Error> {
    // Implementierung
    Ok(result)
}

// ? Schlecht
pub fn myFunction(param:&str)->Result<String,Error>{
    // ...
}
```

**Wichtig**:
- `cargo fmt` vor jedem Commit
- `cargo clippy` muss ohne Warnings durchlaufen
- Dokumentations-Kommentare f?r ?ffentliche APIs

### Python

F?r Python-Code siehe [CONTRIBUTING.md](../CONTRIBUTING.md) im Hauptprojekt.

---

## ?? Testing

### Rust-Tests

```bash
# Unit-Tests
cargo test

# Einzelner Test
cargo test test_get_cpu_count

# Mit Output
cargo test -- --nocapture
```

### Python-Integrationstests

```bash
# Nach Rebuild
cd rust && make dev
cd ..

# Tests ausf?hren
pytest tests/ -k rust -v
```

### Benchmarks

```bash
# Rust-Benchmarks
cargo bench

# Python vs. Rust
python benchmarks/baseline_benchmarks.py
```

---

## ?? Neue Funktion hinzuf?gen

### 1. Rust-Implementation

```rust
// rust/skypilot-utils/src/my_module.rs

use pyo3::prelude::*;

/// Brief description of what this function does.
///
/// # Arguments
/// * `param` - Description of parameter
///
/// # Returns
/// Description of return value
#[pyfunction]
#[pyo3(signature = (param))]
pub fn my_new_function(param: &str) -> PyResult<String> {
    // Implementation
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_my_new_function() {
        let result = my_new_function("test").unwrap();
        assert_eq!(result, "expected");
    }
}
```

### 2. In lib.rs registrieren

```rust
// rust/skypilot-utils/src/lib.rs

pub mod my_module;

#[pymodule]
fn sky_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // ...
    m.add_function(wrap_pyfunction!(my_module::my_new_function, m)?)?;
    Ok(())
}
```

### 3. Python-Wrapper mit Fallback

```python
# sky/utils/rust_fallback.py

def my_new_function(param: str) -> str:
    """Brief description.
    
    Uses Rust implementation for better performance if available.
    """
    if _RUST_AVAILABLE:
        try:
            return sky_rs.my_new_function(param)
        except Exception as e:
            logger.warning(f'Rust my_new_function failed: {e}, using Python fallback')
    
    return _python_my_new_function(param)

def _python_my_new_function(param: str) -> str:
    """Python implementation of my_new_function."""
    # Implementation
    return result
```

### 4. Tests schreiben

```python
# tests/test_my_module.py

import pytest
from sky.utils import rust_fallback

def test_my_new_function():
    result = rust_fallback.my_new_function("test")
    assert result == "expected"

def test_equivalence():
    """Ensure Rust and Python produce same results."""
    param = "test"
    rust_result = rust_fallback.my_new_function(param)
    python_result = rust_fallback._python_my_new_function(param)
    assert rust_result == python_result
```

### 5. Benchmark hinzuf?gen

```rust
// rust/skypilot-utils/benches/my_benchmarks.rs

use criterion::{black_box, criterion_group, criterion_main, Criterion};
use skypilot_utils::my_module::my_new_function;

fn bench_my_new_function(c: &mut Criterion) {
    c.bench_function("my_new_function", |b| {
        b.iter(|| my_new_function(black_box("test")))
    });
}

criterion_group!(benches, bench_my_new_function);
criterion_main!(benches);
```

### 6. Dokumentation aktualisieren

- [ ] Docstring in Rust-Code
- [ ] Python-Docstring
- [ ] README.md erweitern
- [ ] RUST_MIGRATION.md aktualisieren

---

## ?? Code-Review Prozess

### Vor dem Commit

```bash
# Checkliste
make check-all  # Sollte fehlerfrei durchlaufen

# Einzeln
cargo fmt --all         # Code formatieren
cargo clippy --all      # Linter
cargo test             # Tests
cargo build --release  # Release-Build testen
```

### Pull Request

**PR-Titel Format**:
```
[Rust] Brief description of change

Example: [Rust] Add support for async file operations
```

**PR-Beschreibung sollte enthalten**:
1. Was wurde ge?ndert?
2. Warum wurde es ge?ndert?
3. Wie wurde es getestet?
4. Benchmarks (falls Performance-relevant)
5. Breaking Changes (falls vorhanden)

**Checkliste**:
- [ ] Code passes `cargo fmt` and `cargo clippy`
- [ ] Tests added for new functionality
- [ ] Benchmarks updated (if relevant)
- [ ] Documentation updated
- [ ] Python fallback implemented
- [ ] No breaking changes (or well-documented)

---

## ?? Anti-Patterns

### ? Vermeiden Sie

1. **Panics in Production-Code**
   ```rust
   // ? Schlecht
   pub fn bad_function(x: i32) -> i32 {
       x / 0  // panic!
   }
   
   // ? Gut
   pub fn good_function(x: i32) -> PyResult<i32> {
       if x == 0 {
           return Err(PyErr::new::<PyValueError, _>("Division by zero"));
       }
       Ok(1 / x)
   }
   ```

2. **Unwrap() in ?ffentlichen APIs**
   ```rust
   // ? Schlecht
   pub fn bad_function(s: &str) -> String {
       s.parse::<i32>().unwrap().to_string()
   }
   
   // ? Gut
   pub fn good_function(s: &str) -> PyResult<String> {
       let num = s.parse::<i32>()
           .map_err(|e| PyErr::new::<PyValueError, _>(format!("Parse error: {}", e)))?;
       Ok(num.to_string())
   }
   ```

3. **Fehlende Error-Kontext**
   ```rust
   // ? Schlecht
   File::open(path)?
   
   // ? Gut
   File::open(path)
       .map_err(|e| SkyPilotError::IoError(format!("Failed to open {}: {}", path, e)))?
   ```

4. **Python-Objekte in Rust speichern**
   ```rust
   // ? Gef?hrlich - GIL-Issues
   struct BadStruct {
       python_obj: Py<PyAny>,  // Nicht ohne GIL!
   }
   
   // ? Besser - Rust-Typen verwenden
   struct GoodStruct {
       data: String,
   }
   ```

---

## ?? Performance-Richtlinien

### Benchmark-Standards

Neue Funktionen sollten:
- ? Mindestens 2x schneller als Python sein
- ? Benchmarks in `benches/` haben
- ? Performance in PR dokumentieren

### Profiling

```bash
# CPU-Profiling
cargo install flamegraph
cargo flamegraph --bench my_benchmark

# Memory-Profiling
cargo install valgrind
valgrind --tool=massif target/release/skypilot-utils
```

---

## ?? Bug-Reports

### Guter Bug-Report

```markdown
**Beschreibung**
Kurze Beschreibung des Problems

**Reproduktion**
1. Schritt 1
2. Schritt 2
3. Fehler tritt auf

**Erwartetes Verhalten**
Was sollte passieren?

**Tats?chliches Verhalten**
Was passiert stattdessen?

**Environment**
- OS: Linux/macOS/Windows
- Python: 3.x
- Rust: 1.x
- SkyPilot: vX.Y.Z

**Logs/Stacktrace**
```
Relevante Logs hier
```
```

---

## ?? Ressourcen

### Rust

- [Rust Book](https://doc.rust-lang.org/book/)
- [PyO3 Guide](https://pyo3.rs/)
- [Rust by Example](https://doc.rust-lang.org/rust-by-example/)

### SkyPilot

- [Main Contributing Guide](../CONTRIBUTING.md)
- [RUST_MIGRATION.md](../RUST_MIGRATION.md)
- [Project Discussions](https://github.com/skypilot-org/skypilot/discussions)

---

## ?? Danke!

Vielen Dank f?r Ihren Beitrag zu SkyPilot! 

Jeder Beitrag macht das Projekt besser. ??

---

*Fragen? ?ffnen Sie ein Issue oder fragen Sie im Slack-Channel #rust-migration*
