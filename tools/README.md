# SkyPilot Rust Migration - Tools

Praktische Tools zur Unterst?tzung der Rust-Migration.

---

## ??? Verf?gbare Tools

### 1. setup_rust_migration.sh

**Automatisches Setup-Script** - F?hrt komplette Installation und Verifikation durch.

```bash
# Komplettes Setup (5-10 Minuten)
./setup_rust_migration.sh
```

**Was es macht:**
- ? Pr?ft Voraussetzungen (Rust, Python, pip)
- ? Installiert Dependencies (maturin)
- ? Kompiliert Rust-Code (Release-Build)
- ? Installiert Python-Modul
- ? Verifiziert Installation
- ? F?hrt Quick-Benchmark aus

---

### 2. migration_helper.py

**Code-Migrations-Tool** - Analysiert und migriert Python-Code.

```bash
# Datei analysieren
python tools/migration_helper.py sky/utils/some_file.py

# Vorschau der ?nderungen
python tools/migration_helper.py sky/utils/some_file.py --dry-run

# Migration durchf?hren
python tools/migration_helper.py sky/utils/some_file.py --migrate

# Nur pr?fen ob Migration n?tig
python tools/migration_helper.py sky/utils/some_file.py --check
```

**Features:**
- ?? Analysiert Imports und Funktionsaufrufe
- ?? Zeigt Migrations-Potential
- ?? Automatische Code-Migration
- ? Listet verf?gbare Rust-Funktionen

---

### 3. performance_report.py

**Performance-Report-Generator** - Erstellt detaillierte Benchmarks.

```bash
# Console-Output (Standard)
python tools/performance_report.py

# HTML-Report generieren
python tools/performance_report.py --html report.html

# JSON-Export f?r CI
python tools/performance_report.py --json results.json

# Custom Iterations
python tools/performance_report.py --iterations 10000
```

**Output:**
- ?? Vergleicht alle 12 Funktionen
- ? Zeigt Speedup-Faktoren
- ?? Generiert HTML/JSON-Reports
- ?? Summary mit Durchschnittswerten

---

## ?? Weitere Ressourcen

### Dokumentation im Hauptverzeichnis

- **INDEX.md** - Vollst?ndiger Dokumentations-Index
- **QUICKSTART.md** - 5-Minuten-Setup
- **INTEGRATION_GUIDE.md** - Code-Integration
- **RUST_MIGRATION.md** - Vollst?ndiger Guide

### Demos & Beispiele

```
demos/rust_performance_demo.py       # Interactive Performance-Demo
examples/rust_integration_example.py # Praktische Beispiele
benchmarks/baseline_benchmarks.py    # Python vs. Rust Benchmarks
rust/CHECK_INSTALLATION.py           # Installations-Verifikation
```

---

## ?? Typischer Workflow

### Neu installieren

```bash
# 1. Automatisches Setup
./setup_rust_migration.sh

# 2. Verifikation
python rust/CHECK_INSTALLATION.py

# 3. Demo ansehen
python demos/rust_performance_demo.py
```

### Bestehenden Code migrieren

```bash
# 1. Code analysieren
python tools/migration_helper.py my_file.py

# 2. Vorschau der ?nderungen
python tools/migration_helper.py my_file.py --dry-run

# 3. Migration durchf?hren
python tools/migration_helper.py my_file.py --migrate

# 4. Performance messen
python tools/performance_report.py
```

### Performance-Tracking

```bash
# Baseline erstellen
python tools/performance_report.py --json baseline.json

# Nach ?nderungen
python tools/performance_report.py --json current.json

# Vergleichen (manuell oder via CI)
```

---

## ?? Tool-Empfehlungen

### F?r Einsteiger
1. `setup_rust_migration.sh` - Automatisches Setup
2. `rust/CHECK_INSTALLATION.py` - Verifikation
3. `demos/rust_performance_demo.py` - Demo

### F?r Entwickler
1. `migration_helper.py` - Code-Migration
2. `performance_report.py` - Benchmarking
3. `examples/rust_integration_example.py` - Beispiele

### F?r CI/CD
1. `setup_rust_migration.sh` - Automatisiertes Setup
2. `performance_report.py --json` - JSON-Export
3. `migration_helper.py --check` - Exit-Code basiert

---

## ?? Tipps & Tricks

### Quick-Check ob Rust aktiv ist

```bash
python -c "from sky.utils import rust_fallback; print(rust_fallback.get_backend_info())"
```

### Performance-Vergleich f?r eine Funktion

```python
from sky.utils import rust_fallback
import time

# Test function
def benchmark(func, iterations=1000):
    start = time.perf_counter()
    for _ in range(iterations):
        func()
    return (time.perf_counter() - start) / iterations

# Compare
python_time = benchmark(lambda: rust_fallback._python_get_cpu_count())
rust_time = benchmark(lambda: rust_fallback.get_cpu_count())
print(f"Speedup: {python_time/rust_time:.1f}x")
```

### Alle migrierbaren Funktionen finden

```bash
grep -r "from sky.utils.common_utils import" --include="*.py" . | \
  python tools/migration_helper.py --check
```

---

## ?? Troubleshooting

### Tool funktioniert nicht

```bash
# Python-Pfad pr?fen
which python3

# Dependencies pr?fen
python3 -m pip list | grep -E "maturin|pyo3"

# Neu installieren
./setup_rust_migration.sh
```

### Performance-Report zeigt "Python fallback"

```bash
# Rust-Extensions neu installieren
cd rust/skypilot-utils
maturin develop --release

# Verifikation
python rust/CHECK_INSTALLATION.py
```

---

## ?? Support

- **Issues**: GitHub Issues mit Label `rust-migration`
- **Diskussionen**: GitHub Discussions
- **Email**: engineering@skypilot.co

---

*Letzte Aktualisierung: 2024-10-31*
