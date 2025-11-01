# ?? Styx - Git Branch Setup & Workflow

**Project**: Styx (ehemals SkyPilot-R)  
**Date**: 2024-10-31  
**Purpose**: Git-Branch-Struktur f?r Rust-Rewrite

---

## ?? Branch-Strategie

### Branches

```
???????????????????????????????????????????????????
?  skypilot (main)                                ?
?  ?                                              ?
?  ? Original Python SkyPilot                    ?
?  ? Bleibt als Upstream/Reference               ?
???????????????????????????????????????????????????
   ?
   ?? merge/sync bei Bedarf
   ?
???????????????????????????????????????????????????
?  styx (main f?r Rust)                           ?
?  ?                                              ?
?  ? Kompletter Rust Rewrite                     ?
?  ? Produktions-Branch                          ?
???????????????????????????????????????????????????
   ?
   ?? feature branches
   ?
   ?? styx/phase-1-core      (? merged)
   ?? styx/phase-2-cloud     (?? active)
   ?? styx/phase-3-server    (? next)
   ?? styx/phase-4-ui        (? future)
```

---

## ?? Git Setup Commands

### 1. Initial Repository Setup

```bash
# Navigiere zum Repository
cd /workspace

# Falls noch nicht initialisiert:
git init

# Setze Remote (wenn vorhanden)
git remote add origin <repository-url>
```

### 2. Branch Creation

```bash
# Erstelle skypilot Branch (Python Original)
git checkout -b skypilot
git add sky/ tests/ examples/ *.py *.md
git commit -m "Initial commit: Python SkyPilot (original)"
git push -u origin skypilot

# Erstelle styx Branch (Rust Rewrite)
git checkout -b styx
git add skypilot-r/
git commit -m "Initial commit: Styx - Rust rewrite of SkyPilot"
git push -u origin styx
```

### 3. Branch Protection Rules

```bash
# Auf GitHub/GitLab/Gitea einrichten:

# F?r Branch: skypilot
- Branch protection: ON
- Require PR reviews: 1
- Require status checks: CI/CD
- Restrict direct pushes: YES
- Allow force push: NO

# F?r Branch: styx
- Branch protection: ON
- Require PR reviews: 1
- Require status checks: Rust CI, Tests
- Restrict direct pushes: YES (au?er f?r maintainers)
- Allow force push: NO
```

---

## ?? Workflow-Regeln

### Regel 1: Branch-Zwecke

```yaml
skypilot:
  purpose: "Python SkyPilot - Original/Upstream"
  language: Python
  maintains: "Bestehende Features, Bug-fixes"
  stability: Production
  
styx:
  purpose: "Styx - Rust Rewrite (vollst?ndig neu)"
  language: Rust
  maintains: "Neue Rust-Implementation"
  stability: Development ? Production
```

### Regel 2: Merge-Direction

```
ERLAUBT:
  skypilot ? styx    (sync upstream changes, selektiv)
  
NICHT ERLAUBT:
  styx ? skypilot    (komplett separate Codebases)
```

### Regel 3: Feature Branches

```bash
# F?r Styx-Features:
git checkout styx
git checkout -b styx/feature-name

# Nach Fertigstellung:
git checkout styx
git merge --no-ff styx/feature-name
git branch -d styx/feature-name

# F?r SkyPilot (Python):
git checkout skypilot
git checkout -b skypilot/feature-name

# Nach Fertigstellung:
git checkout skypilot
git merge --no-ff skypilot/feature-name
```

### Regel 4: Commit-Conventions

```bash
# Styx commits:
[Styx] <type>: <description>

Typen:
- [Styx] feat: Neue Feature
- [Styx] fix: Bug-fix
- [Styx] refactor: Code-Refactoring
- [Styx] docs: Dokumentation
- [Styx] test: Tests
- [Styx] chore: Build/Tools

Beispiele:
git commit -m "[Styx] feat(cloud): Add AWS provider implementation"
git commit -m "[Styx] fix(scheduler): Resolve deadlock in task queue"
git commit -m "[Styx] docs: Update Phase 2 progress"

# SkyPilot commits:
[SkyPilot] <type>: <description>

Beispiel:
git commit -m "[SkyPilot] fix: Resolve authentication issue in GCP"
```

---

## ?? Aktueller Stand

### Was bereits existiert:

```
/workspace/
??? sky/                    # Python SkyPilot (original)
??? tests/                  # Python tests
??? examples/               # Python examples
??? skypilot-r/            # Rust code (rename zu styx/)
??? [weitere Python files]
```

### Was zu tun ist:

1. **Rebranding**: `skypilot-r/` ? `styx/`
2. **Git Setup**: Branches erstellen
3. **CI/CD**: Separate Workflows
4. **Dokumentation**: Updaten

---

## ?? Setup-Schritte f?r Copilot/Team

### Schritt 1: Rebranding durchf?hren

```bash
# Workspace verschieben
cd /workspace
mv skypilot-r styx

# Alle Referenzen updaten
find styx -type f -name "*.toml" -o -name "*.rs" -o -name "*.md" | \
  xargs sed -i 's/skypilot-r/styx/g'
  
find styx -type f -name "*.toml" -o -name "*.rs" -o -name "*.md" | \
  xargs sed -i 's/SkyPilot-R/Styx/g'
  
find styx -type f -name "*.toml" -o -name "*.rs" -o -name "*.md" | \
  xargs sed -i 's/skypilot_r/styx/g'
```

### Schritt 2: Git Branches einrichten

```bash
# Sichere aktuellen Stand
git stash

# Erstelle skypilot Branch (Python)
git checkout -b skypilot
git add .
git commit -m "[SkyPilot] Initial commit: Python SkyPilot codebase"

# Erstelle styx Branch (Rust)
git checkout -b styx skypilot
# L?sche Python-only files aus styx branch
git rm -rf sky/ tests/ examples/ *.py
git add styx/
git commit -m "[Styx] Initial commit: Rust rewrite - Phases 1-2 complete"

# Push beide branches
git push -u origin skypilot
git push -u origin styx
```

### Schritt 3: CI/CD Setup

Erstelle `.github/workflows/styx-ci.yml`:

```yaml
name: Styx CI

on:
  push:
    branches: [styx, 'styx/**']
  pull_request:
    branches: [styx]

jobs:
  rust-ci:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
      
      - name: Format check
        run: cd styx && cargo fmt --all --check
      
      - name: Clippy
        run: cd styx && cargo clippy --all-targets
      
      - name: Build
        run: cd styx && cargo build --release
      
      - name: Test
        run: cd styx && cargo test --all
```

Behalte `.github/workflows/skypilot-ci.yml` f?r Python.

### Schritt 4: README Updates

Erstelle `/workspace/README.md` (Root):

```markdown
# SkyPilot Project

This repository contains two implementations:

## ?? [SkyPilot](./README_SKYPILOT.md) (Python - Branch: `skypilot`)
Original Python implementation - Production ready
- Branch: `skypilot`
- Language: Python 3.8+
- Status: Stable

## ?? [Styx](./styx/README.md) (Rust - Branch: `styx`)
Complete Rust rewrite - High-performance cloud orchestration
- Branch: `styx`
- Language: Rust 1.75+
- Status: Active Development (Phase 2/5)

---

**Default Branch**: `skypilot` (stable)  
**Development Branch**: `styx` (rust rewrite)
```

### Schritt 5: Branch Protection

Auf GitHub/GitLab:

```yaml
Protected Branches:
  
  skypilot:
    - Require pull request reviews before merging: 1
    - Require status checks to pass before merging: 
        - Python Tests
        - Python Lint
    - Require branches to be up to date before merging
    - Do not allow bypassing the above settings
    
  styx:
    - Require pull request reviews before merging: 1
    - Require status checks to pass before merging:
        - Rust Format Check
        - Rust Clippy
        - Rust Tests
        - Rust Build
    - Require branches to be up to date before merging
    - Do not allow bypassing the above settings
```

---

## ?? CODEOWNERS Setup

Erstelle `.github/CODEOWNERS`:

```
# SkyPilot Python (original)
/sky/**                  @skypilot-team-python
/tests/**                @skypilot-team-python
*.py                     @skypilot-team-python

# Styx Rust (rewrite)
/styx/**                 @styx-team-rust
/styx/crates/core/**     @styx-core-maintainers
/styx/crates/cloud/**    @styx-cloud-maintainers
*.rs                     @styx-team-rust

# Documentation
*.md                     @docs-team
```

---

## ?? Sync-Strategie

### Von skypilot nach styx synchronisieren

```bash
# Selektiv Features/Bugfixes ?bernehmen

# 1. Checkout styx
git checkout styx

# 2. Cherry-pick specific commits von skypilot
git cherry-pick <commit-hash>

# ODER: Merge specific files
git checkout skypilot -- path/to/specific/file
# Dann manuell zu Rust konvertieren
```

### NICHT von styx nach skypilot mergen

```bash
# VERBOTEN - komplett separate Codebases
git checkout skypilot
git merge styx  # ? NIEMALS MACHEN
```

---

## ??? Tagging-Strategie

```bash
# SkyPilot (Python) releases:
git tag -a v1.0.0-python -m "SkyPilot Python v1.0.0"
git push origin v1.0.0-python

# Styx (Rust) releases:
git tag -a v0.1.0-alpha -m "Styx Rust v0.1.0-alpha - Phase 1-2 complete"
git push origin v0.1.0-alpha

# Sp?ter:
git tag -a v1.0.0 -m "Styx v1.0.0 - Production ready"
git push origin v1.0.0
```

---

## ?? Quick Reference

### F?r SkyPilot (Python) Entwicklung:

```bash
git checkout skypilot
git checkout -b skypilot/my-feature
# ... entwickeln ...
git commit -m "[SkyPilot] feat: my feature"
git push origin skypilot/my-feature
# PR erstellen auf GitHub: skypilot/my-feature ? skypilot
```

### F?r Styx (Rust) Entwicklung:

```bash
git checkout styx
git checkout -b styx/my-feature
# ... entwickeln ...
git commit -m "[Styx] feat: my feature"
git push origin styx/my-feature
# PR erstellen auf GitHub: styx/my-feature ? styx
```

### Branch Status pr?fen:

```bash
git branch -a                    # Alle branches zeigen
git log --oneline --graph --all  # Branch-Historie visualisieren
```

---

## ?? Branch-?bersicht

| Branch | Language | Status | Purpose | Protected |
|--------|----------|--------|---------|-----------|
| `skypilot` | Python | Stable | Original SkyPilot | ? Yes |
| `styx` | Rust | Development | Complete rewrite | ? Yes |
| `styx/phase-*` | Rust | Feature | Phase development | ? No |
| `skypilot/feature-*` | Python | Feature | Python features | ? No |

---

## ? Checkliste f?r Setup

- [ ] Rebranding: `skypilot-r` ? `styx`
- [ ] Branch `skypilot` erstellen
- [ ] Branch `styx` erstellen
- [ ] CI/CD Workflows anpassen
- [ ] Branch protection rules setzen
- [ ] CODEOWNERS erstellen
- [ ] README updates
- [ ] Team informieren
- [ ] Dokumentation updaten

---

## ?? Fertig!

Nach diesem Setup hast du:

? **Zwei unabh?ngige Branches** (skypilot, styx)  
? **Klare Branch-Protection**  
? **Separate CI/CD Pipelines**  
? **Saubere Commit-Conventions**  
? **CODEOWNERS f?r Reviews**  
? **Dokumentierte Workflows**  

---

**Repository**: `skypilot-org/skypilot`  
**Main Branch (Python)**: `skypilot`  
**Development Branch (Rust)**: `styx`  

**Ready to ship!** ????
