# README Addendum - Rust Performance Extensions

**Hinweis**: Dieser Inhalt sollte in die Haupt-README.md integriert werden.

---

## ⚡ Performance Boost mit Rust (Optional)

SkyPilot bietet jetzt **optionale Rust-Extensions** für bis zu **25x schnellere** Performance bei bestimmten Operationen.

### Installation

```bash
# Standard-Installation (Python)
pip install skypilot

# Mit Rust-Optimierungen (empfohlen für Production)
cd rust/skypilot-utils
maturin develop --release
```

### Performance-Verbesserungen

| Operation | Speedup |
|-----------|---------|
| Process-Management | 5-25x ⚡ |
| File-Operationen | 3-7x ⚡ |
| System-Abfragen | 7-20x ⚡ |
| String-Operationen | 3-10x ⚡ |

**Zero Configuration**: Rust wird automatisch verwendet wenn verfügbar, mit transparentem Python-Fallback.

### Quick Start

```bash
# Installation prüfen
python rust/CHECK_INSTALLATION.py

# Performance-Demo
python demos/rust_performance_demo.py --quick

# Benchmarks
python benchmarks/baseline_benchmarks.py
```

### Dokumentation

- 📖 [Rust Quick Start](rust/QUICKSTART.md)
- 📖 [Migration Guide](RUST_MIGRATION.md)
- 📖 [Installation Details](rust/INSTALL.md)

### Kompatibilität

- ✅ **Linux**: Full support (Ubuntu 20.04+)
- ✅ **macOS**: Full support (11+)
- ⏳ **Windows**: Coming Q2 2025
- 🐍 **Python**: 3.8 - 3.12

**Opt-out**: `export SKYPILOT_USE_RUST=0`

---

*Die Rust-Extensions sind vollständig optional und ändern nichts am bestehenden API.*
