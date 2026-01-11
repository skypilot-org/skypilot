# Migration Plan: yapf/mypy to ruff/ty

This document outlines a phased migration strategy for transitioning SkyPilot's linting and type-checking infrastructure from yapf/mypy to ruff/ty, designed to minimize disruption to ongoing development.

## Executive Summary

**Current Stack:**
- yapf 0.32.0 (formatting - Google style)
- black 22.10.0 (formatting - IBM provider only)
- isort 5.12.0 (import sorting)
- mypy 1.14.1 (type checking)
- pylint 2.14.5 + pylint-quotes 0.2.3 (linting)

**Target Stack:**
- ruff (formatting + linting + import sorting - replaces yapf, black, isort, pylint)
- ty (type checking - replaces mypy)

**Key Benefits:**
- 10-100x faster formatting and linting
- Single tool for formatting + linting + import sorting
- Better error messages and auto-fixes
- Active development and modern Python support
- Reduced dependency footprint (5 tools → 2 tools)

## Migration Phases

### Phase 0: Preparation (Week 1)

**Goal:** Set up infrastructure for parallel tooling without affecting existing workflows.

#### 0.1 Create Baseline Configurations

1. **Create `ruff.toml`** with equivalent rules:
   ```toml
   # ruff.toml
   target-version = "py38"
   line-length = 80

   [format]
   quote-style = "single"
   indent-style = "space"
   docstring-code-format = true

   [lint]
   select = [
       "E",      # pycodestyle errors
       "W",      # pycodestyle warnings
       "F",      # pyflakes
       "I",      # isort
       "UP",     # pyupgrade
       "YTT",    # flake8-2020
       "B",      # flake8-bugbear
       "C4",     # flake8-comprehensions
       "Q",      # flake8-quotes
       "SIM",    # flake8-simplify
       "PL",     # pylint rules
   ]
   ignore = [
       # Map from .pylintrc disabled checks
       "PLR0913",  # too-many-arguments
       "PLR0912",  # too-many-branches
       "PLR0915",  # too-many-statements
       "PLR2004",  # magic-value-comparison
       "PLW0603",  # global-statement
       # ... additional mappings from .pylintrc
   ]

   [lint.isort]
   combine-as-imports = true
   force-single-line = false
   known-first-party = ["sky"]

   [lint.flake8-quotes]
   inline-quotes = "single"
   docstring-quotes = "double"

   [lint.per-file-ignores]
   # IBM provider uses different style
   "sky/skylet/providers/ibm/*" = ["Q000"]  # Allow double quotes
   ```

2. **Create `ty.toml`** (or pyproject.toml section):
   ```toml
   # ty.toml
   [tool.ty]
   python-version = "3.8"
   # Equivalent mypy settings
   ```

3. **Add new dependencies to `requirements-dev.txt`** (alongside existing):
   ```
   # New tools (Phase 0 - parallel installation)
   ruff>=0.8.0
   ty>=0.1.0  # Or latest stable version
   ```

#### 0.2 Create Parallel Tooling Script

Create `format_ruff.sh` for testing without affecting existing workflow:
```bash
#!/usr/bin/env bash
# Parallel formatting script for migration testing
# Does NOT replace format.sh yet

set -eo pipefail

RUFF_VERSION=$(ruff --version | head -1 | awk '{print $2}')
echo "Running ruff $RUFF_VERSION (migration testing)"

# Format
ruff format .

# Lint with auto-fix
ruff check --fix .

echo "Ruff formatting complete (testing mode)"
```

#### 0.3 Document Differences

Create a mapping document of any behavioral differences between:
- yapf Google style vs ruff format
- isort Google profile vs ruff isort
- pylint rules vs ruff lint rules
- mypy checks vs ty checks

---

### Phase 1: Ruff Adoption - Lint Only (Weeks 2-3)

**Goal:** Introduce ruff for linting alongside pylint, then replace pylint.

#### 1.1 Add Ruff Linting to CI (Non-blocking)

Add `.github/workflows/ruff-lint.yml`:
```yaml
name: Ruff Lint (Experimental)
on: [push, pull_request]
jobs:
  ruff:
    runs-on: ubuntu-latest
    continue-on-error: true  # Non-blocking initially
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/ruff-action@v2
        with:
          args: "check --output-format=github"
```

#### 1.2 Tune Ruff Rules

Over 1-2 weeks:
1. Run ruff on the codebase and collect violations
2. Decide per-rule: fix violations or add to ignore list
3. Update `ruff.toml` to match project conventions
4. Create PRs to fix auto-fixable issues in batches

#### 1.3 Replace Pylint with Ruff Lint

Once ruff lint rules are stable:

1. **Update CI:** Change `ruff-lint.yml` from `continue-on-error: true` to blocking
2. **Update `.pre-commit-config.yaml`:**
   ```yaml
   - repo: https://github.com/astral-sh/ruff-pre-commit
     rev: v0.8.0
     hooks:
       - id: ruff
         args: [--fix]
   ```
3. **Remove pylint from CI** (`.github/workflows/pylint.yml`)
4. **Update `format.sh`:** Remove pylint invocation, add ruff check

**Announcement:** Post in PR/Slack that pylint is being replaced. Give 1-week notice.

---

### Phase 2: Ruff Adoption - Formatting (Weeks 4-6)

**Goal:** Migrate from yapf/black/isort to ruff format.

#### 2.1 Achieve Format Parity

The biggest challenge: ensuring ruff format produces similar output to yapf.

1. **Run comparison script:**
   ```bash
   # Format with yapf, save diff
   git stash
   bash format.sh --all
   git diff > yapf_format.diff
   git checkout .

   # Format with ruff, save diff
   ruff format .
   git diff > ruff_format.diff

   # Compare
   diff yapf_format.diff ruff_format.diff
   ```

2. **Identify differences** and decide:
   - Accept ruff's style (preferred - it's more consistent)
   - Configure ruff to match yapf where critical
   - Document intentional style changes

3. **Handle IBM provider exception:**
   - IBM code currently uses Black with 88-char lines
   - Options:
     a. Keep separate formatting for IBM (ruff supports per-directory config)
     b. Migrate IBM code to common style (requires IBM team approval)

   ```toml
   # ruff.toml - per-directory override
   [format]
   line-length = 80

   # Override for IBM
   ["sky/skylet/providers/ibm/*"]
   line-length = 88
   ```

#### 2.2 The Big Format PR

**Critical step:** One large PR that reformats the entire codebase.

**Timing:** Schedule for a low-activity period (weekend or after a release).

**Process:**
1. **Announce 48-72 hours in advance:**
   ```
   Subject: [Migration] Formatter change: yapf → ruff (Landing DATE)

   We're migrating from yapf to ruff for code formatting.

   Impact:
   - All Python files will be reformatted
   - Existing PRs may have merge conflicts
   - After DATE, run `ruff format` instead of `yapf`

   To prepare your PRs:
   1. Rebase on master before DATE
   2. After the format PR lands, rebase again and run:
      ruff format <your-changed-files>
   ```

2. **Create the format PR:**
   ```bash
   git checkout -b migrate-to-ruff-format
   ruff format .
   ruff check --fix .  # Auto-fix any lint issues
   git add -A
   git commit -m "[Infra] Migrate code formatting from yapf to ruff"
   ```

3. **Review strategy:**
   - Do NOT review individual file changes (too large)
   - Verify CI passes
   - Spot-check critical files
   - Merge with minimal delay

4. **Post-merge:** Help contributors rebase their PRs

#### 2.3 Update All Tooling

After the format PR lands:

1. **Update `format.sh`:**
   ```bash
   #!/usr/bin/env bash
   set -eo pipefail

   SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
   cd "$SCRIPT_DIR"

   # Version check
   RUFF_VERSION_REQUIRED="0.8.0"
   RUFF_VERSION=$(ruff --version | head -1 | awk '{print $2}')
   if [[ "$RUFF_VERSION" != "$RUFF_VERSION_REQUIRED" ]]; then
       echo "Wrong ruff version: $RUFF_VERSION (expected $RUFF_VERSION_REQUIRED)"
       exit 1
   fi

   # Determine files to format
   if [[ "$1" == "--all" ]]; then
       FILES="."
   elif [[ "$1" == "--files" ]]; then
       shift
       FILES="$@"
   else
       MERGEBASE=$(git merge-base origin/master HEAD)
       FILES=$(git diff --name-only --diff-filter=ACM "$MERGEBASE" -- '*.py')
   fi

   if [[ -z "$FILES" ]]; then
       echo "No Python files to format"
       exit 0
   fi

   # Format
   echo "Running ruff format..."
   ruff format $FILES

   # Lint with auto-fix
   echo "Running ruff check..."
   ruff check --fix $FILES

   # Check for modifications
   if ! git diff --quiet; then
       echo "Files were modified. Please review and stage changes."
       exit 1
   fi

   echo "All checks passed!"
   ```

2. **Update `.pre-commit-config.yaml`:**
   ```yaml
   repos:
     - repo: https://github.com/pre-commit/pre-commit-hooks
       rev: v5.0.0
       hooks:
         - id: trailing-whitespace
         - id: end-of-file-fixer
         - id: check-yaml
           exclude: charts/
         - id: check-added-large-files

     - repo: https://github.com/astral-sh/ruff-pre-commit
       rev: v0.8.0
       hooks:
         - id: ruff-format
         - id: ruff
           args: [--fix]
   ```

3. **Update `.github/workflows/format.yml`:**
   ```yaml
   name: Format Check
   on: [push, pull_request, merge_group]
   jobs:
     format:
       runs-on: ubuntu-latest
       steps:
         - uses: actions/checkout@v4
         - uses: astral-sh/ruff-action@v2
           with:
             args: "format --check"
         - uses: astral-sh/ruff-action@v2
           with:
             args: "check"
   ```

4. **Update `requirements-dev.txt`:**
   ```
   # Remove old tools
   # yapf==0.32.0  (removed)
   # black==22.10.0  (removed)
   # isort==5.12.0  (removed)
   # pylint==2.14.5  (removed)
   # pylint-quotes==0.2.3  (removed)

   # New unified tool
   ruff==0.8.0
   ```

5. **Update `pyproject.toml`:**
   - Remove `[tool.yapf]` section
   - Remove `[tool.isort]` section
   - Add `[tool.ruff]` section (or reference ruff.toml)

6. **Remove obsolete files:**
   - `.pylintrc` → rules migrated to ruff.toml
   - Delete old GitHub workflows

---

### Phase 3: ty Adoption (Weeks 7-9)

**Goal:** Migrate from mypy to ty for type checking.

> **Note:** ty is newer and may have different behavior than mypy. This phase requires more careful testing.

#### 3.1 Evaluate ty Readiness

Before migrating:
1. Run ty on the codebase and compare output to mypy
2. Identify any type errors found by ty but not mypy (and vice versa)
3. Assess ty's stability and feature completeness
4. Check ty supports all features used (Python 3.8+, stub packages)

```bash
# Comparison script
mypy $(cat tests/mypy_files.txt) > mypy_errors.txt 2>&1 || true
ty check sky/ > ty_errors.txt 2>&1 || true
diff mypy_errors.txt ty_errors.txt
```

#### 3.2 Add ty to CI (Non-blocking)

```yaml
# .github/workflows/ty.yml
name: Type Check (ty - Experimental)
on: [push, pull_request]
jobs:
  ty:
    runs-on: ubuntu-latest
    continue-on-error: true
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-ty@v1
      - run: ty check sky/
```

#### 3.3 Fix Type Differences

Address any new errors found by ty:
1. Fix legitimate type issues
2. Add `# type: ignore[ty-specific-code]` for false positives
3. Update ty configuration to match project needs

#### 3.4 Replace mypy with ty

Once ty produces equivalent (or better) results:

1. **Update `format.sh`:** Replace mypy invocation with ty
2. **Update CI:** Replace mypy.yml with ty.yml (blocking)
3. **Update pre-commit:** Replace mypy hook with ty hook
4. **Update `requirements-dev.txt`:**
   ```
   # Remove
   # mypy==1.14.1
   # types-PyYAML
   # types-paramiko
   # ... other type stubs

   # Add
   ty>=0.1.0
   ```

5. **Remove `tests/mypy_files.txt`** (ty may use different config)

---

### Phase 4: Cleanup and Documentation (Week 10)

#### 4.1 Final Cleanup

1. Remove all references to old tools from documentation
2. Update CLAUDE.md with new tooling instructions
3. Update CONTRIBUTING.md
4. Archive migration tracking issues

#### 4.2 Update Developer Documentation

Update `CLAUDE.md`:
```markdown
## Code Formatting and Linting

**Always run `format.sh` before committing:**

```bash
bash format.sh         # Format changed files (vs origin/master)
bash format.sh --all   # Format entire codebase
bash format.sh --files path/to/file.py  # Format specific files
```

The script runs:
1. **ruff format** - Code formatting (replaces yapf/black/isort)
2. **ruff check** - Linting with auto-fix (replaces pylint)
3. **ty check** - Type checking (replaces mypy)

### Tool Versions (must match exactly)
From `requirements-dev.txt`:
- ruff==0.8.0
- ty==0.1.0
```

---

## Risk Mitigation

### Handling Existing PRs

**Before the format migration PR:**
1. Announce 48-72 hours in advance
2. Encourage contributors to merge or rebase PRs
3. Provide clear instructions for post-migration rebase

**After the format migration PR:**
1. Provide helper script:
   ```bash
   # rebase-to-ruff.sh
   git fetch origin master
   git rebase origin/master
   # If conflicts in formatting:
   git checkout --theirs .
   ruff format .
   ruff check --fix .
   git add -A
   git rebase --continue
   ```

2. Offer to help rebase long-running PRs
3. Be patient with contributors who encounter issues

### Rollback Plan

If critical issues are discovered:

1. **For ruff format issues:**
   - Revert the format PR
   - Re-enable yapf/black/isort in CI and format.sh
   - File issues with ruff project

2. **For ty issues:**
   - Re-enable mypy in CI
   - Keep ty as non-blocking until fixed

### Known Challenges

1. **Import order differences:** ruff's isort may order imports differently than isort. Accept ruff's ordering.

2. **Quote style:** Both tools support single quotes, but edge cases may differ. Configure ruff to match.

3. **Line length edge cases:** Different algorithms for line breaking. Accept ruff's decisions.

4. **IBM provider:** Maintain separate formatting rules via per-file config.

5. **Type stub compatibility:** ty may not support all type stubs. May need to keep some types-* packages.

---

## Timeline Summary

| Week | Phase | Key Milestone |
|------|-------|---------------|
| 1 | Phase 0 | Baseline configs created, parallel scripts ready |
| 2-3 | Phase 1 | Ruff linting active, pylint removed |
| 4 | Phase 2a | Format parity achieved |
| 5 | Phase 2b | **Big format PR lands** |
| 6 | Phase 2c | All tooling updated to ruff |
| 7-8 | Phase 3a | ty evaluation and CI integration |
| 9 | Phase 3b | mypy replaced with ty |
| 10 | Phase 4 | Cleanup and documentation |

---

## Success Criteria

- [ ] `format.sh` uses only ruff and ty
- [ ] CI uses only ruff and ty
- [ ] Pre-commit uses only ruff and ty
- [ ] No yapf, black, isort, pylint, or mypy in requirements-dev.txt
- [ ] All documentation updated
- [ ] Developer experience improved (faster checks)
- [ ] No regression in code quality checks

---

## Appendix A: Rule Mapping (pylint → ruff)

| pylint rule | ruff code | Status |
|-------------|-----------|--------|
| `unused-import` | F401 | Enabled |
| `unused-variable` | F841 | Enabled |
| `line-too-long` | E501 | Enabled |
| `trailing-whitespace` | W291 | Enabled |
| `missing-docstring` | D100-D107 | Disabled (match current) |
| `too-many-arguments` | PLR0913 | Disabled |
| `too-many-branches` | PLR0912 | Disabled |
| `too-many-statements` | PLR0915 | Disabled |
| `consider-using-f-string` | UP032 | Disabled (match current) |
| ... | ... | ... |

See full mapping in `ruff.toml` comments.

---

## Appendix B: Commands Reference

```bash
# Old workflow
bash format.sh
mypy $(cat tests/mypy_files.txt)
pylint sky/

# New workflow
bash format.sh  # Now uses ruff + ty internally

# Or manually:
ruff format .
ruff check --fix .
ty check sky/
```

---

## Appendix C: Configuration File Changes

### Files to Create
- `ruff.toml` - Ruff configuration
- `ty.toml` (or pyproject.toml section) - ty configuration

### Files to Modify
- `format.sh` - Use ruff/ty instead of yapf/mypy/pylint
- `pyproject.toml` - Remove old tool configs, add new
- `requirements-dev.txt` - Swap dependencies
- `.pre-commit-config.yaml` - Update hooks
- `.github/workflows/*.yml` - Update CI
- `CLAUDE.md` - Update dev instructions
- `docs/source/developers/CONTRIBUTING.md` - Update contributor guide

### Files to Delete
- `.pylintrc` - Rules migrated to ruff.toml
- `tests/mypy_files.txt` - ty uses different config
- `.github/workflows/pylint.yml` - Replaced by ruff
- `.github/workflows/mypy.yml` - Replaced by ty
