# Installation Guide - Rust Utilities f?r SkyPilot

## F?r Endnutzer

### Option 1: Pip Installation (empfohlen)

Wenn SkyPilot mit Rust-Support ver?ffentlicht wird:

```bash
pip install skypilot[rust]
```

### Option 2: Aus Source mit Rust-Support

```bash
# Rust installieren (falls noch nicht vorhanden)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Repository klonen
git clone https://github.com/skypilot-org/skypilot.git
cd skypilot

# Maturin installieren
pip install maturin

# Rust-Modul bauen und installieren
cd rust/skypilot-utils
maturin develop --release

# SkyPilot installieren
cd ../..
pip install -e .
```

### Option 3: Ohne Rust-Support

SkyPilot funktioniert auch ohne Rust-Extensions:

```bash
pip install skypilot
```

Der Python-Fallback wird automatisch verwendet.

## F?r Entwickler

### Entwicklungsumgebung einrichten

```bash
# 1. Rust-Toolchain installieren
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source $HOME/.cargo/env

# 2. Entwickler-Tools installieren
rustup component add rustfmt clippy
cargo install cargo-audit cargo-watch

# 3. Python-Dependencies
pip install maturin pytest pytest-benchmark

# 4. SkyPilot in Development-Mode
cd /path/to/skypilot
pip install -e .

# 5. Rust-Modul im Development-Mode
cd rust/skypilot-utils
maturin develop
```

### Schneller Entwicklungszyklus

```bash
# Terminal 1: Auto-Rebuild bei ?nderungen
cd rust
cargo watch -x 'build --lib'

# Terminal 2: Python-Tests
cd /path/to/skypilot
pytest tests/ -k rust
```

### Rebuild nach Code-?nderungen

```bash
cd rust/skypilot-utils
maturin develop        # Debug-Build (schnell, f?r Entwicklung)
maturin develop --release  # Release-Build (langsam, f?r Benchmarks)
```

## Rust-Extensions deaktivieren

Manchmal m?chten Sie tempor?r die Python-Fallbacks verwenden:

```bash
export SKYPILOT_USE_RUST=0
```

Oder im Code:

```python
import os
os.environ['SKYPILOT_USE_RUST'] = '0'

from sky.utils import rust_fallback
# Verwendet jetzt Python-Implementierungen
```

## Verifizierung

```python
# Pr?fen, ob Rust verf?gbar ist
from sky.utils import rust_fallback

if rust_fallback.is_rust_available():
    print("? Rust extensions loaded!")
    info = rust_fallback.get_backend_info()
    print(f"  Version: {info['version']}")
else:
    print("? Rust extensions not available, using Python fallback")
```

## Troubleshooting

### Import-Fehler: `No module named 'sky_rs'`

**Problem:** Rust-Modul wurde nicht gebaut.

**L?sung:**
```bash
cd rust/skypilot-utils
maturin develop
```

### Linking-Fehler bei Tests

**Problem:** PyO3-Tests ben?tigen Python-Kontext.

**L?sung:** Verwenden Sie Python-Tests statt reiner Rust-Tests:
```bash
# Nicht: cargo test
# Sondern:
pytest tests/ -k rust
```

### Performance schlechter als erwartet

**Problem:** Debug-Build statt Release-Build.

**L?sung:**
```bash
cd rust/skypilot-utils
maturin develop --release
```

### macOS: Library not loaded Fehler

**Problem:** Python-Framework-Link fehlt.

**L?sung:**
```bash
# Python von python.org verwenden, nicht System-Python
brew install python@3.11
```

### Linux: `undefined reference to ...` Fehler

**Problem:** Falsche manylinux-Version.

**L?sung:**
```bash
# F?r ?ltere Linux-Distributionen:
maturin build --manylinux 2_17
```

## Platform-spezifische Hinweise

### Linux

- Empfohlen: Ubuntu 20.04+ oder ?quivalent
- Ben?tigt: glibc 2.28+
- F?r ?ltere Systeme: Verwenden Sie manylinux2014

### macOS

- Empfohlen: macOS 11+
- Unterst?tzt: Intel (x86_64) und Apple Silicon (aarch64)
- Python von Homebrew oder python.org empfohlen

### Windows

?? **Experimentell** - Windows-Support ist noch in Arbeit.

Erwartete Unterst?tzung: Q2 2025

## Build-Konfiguration

### Release-Build mit Optimierungen

```bash
cd rust
RUSTFLAGS="-C target-cpu=native" cargo build --release
cd skypilot-utils
maturin develop --release
```

### Cross-Compilation (fortgeschritten)

```bash
# F?r andere Architekturen
rustup target add aarch64-unknown-linux-gnu
cargo build --target aarch64-unknown-linux-gnu --release
```

## CI/CD Integration

Siehe `.github/workflows/rust-ci.yml` f?r automatisierte Builds und Tests.

## Weitere Hilfe

- ?? [Migrations-Guide](../RUST_MIGRATION.md)
- ?? [Issues](https://github.com/skypilot-org/skypilot/issues)
- ?? [Discussions](https://github.com/skypilot-org/skypilot/discussions)
