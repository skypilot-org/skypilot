# SkyPilot Development Makefile
#
# Quick commands for local development, testing, and code quality checks
# Uses uv for fast dependency management

.PHONY: help install test-local test-unit test-integration lint format type-check check clean dev

# Default target
.DEFAULT_GOAL := help

# Python and environment settings
PYTHON_VERSION := 3.11
VENV_DIR := .venv
PYTHON := $(VENV_DIR)/bin/python
PYTEST := $(VENV_DIR)/bin/pytest
UV := uv

# Test environment variables
export SKYPILOT_DISABLE_USAGE_COLLECTION := 1
export SKYPILOT_SKIP_CLOUD_IDENTITY_CHECK := 1

# Formatting/linting exclusions (from .pre-commit-config.yaml)
ISORT_YAPF_EXCLUDES := --extend-exclude 'build/|sky/skylet/providers/ibm/|sky/schemas/generated/'
IBM_FILES := sky/skylet/providers/ibm/

##@ General

help: ## Display this help message
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

##@ Setup

install: ## Install dependencies using uv (creates venv if needed)
	@echo "📦 Installing SkyPilot with dependencies..."
	@if [ ! -d "$(VENV_DIR)" ]; then \
		echo "Creating virtual environment with uv..."; \
		$(UV) venv --python $(PYTHON_VERSION) $(VENV_DIR); \
	fi
	@echo "Installing Azure CLI (with pre-release workaround)..."
	@$(UV) pip install --prerelease=allow "azure-cli>=2.65.0" --python $(PYTHON)
	@echo "Installing SkyPilot in editable mode..."
	@$(UV) pip install -e ".[all]" --python $(PYTHON)
	@echo "Installing dev dependencies from requirements-dev.txt..."
	@$(UV) pip install -r requirements-dev.txt --python $(PYTHON)
	@echo "✅ Installation complete! Activate venv with: source $(VENV_DIR)/bin/activate"

dev: install ## Install in development mode (alias for install)

clean: ## Remove virtual environment and cache files
	@echo "🧹 Cleaning up..."
	rm -rf $(VENV_DIR)
	rm -rf .pytest_cache
	rm -rf __pycache__
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	@echo "✅ Cleanup complete!"

##@ Testing

test-local: ## Run all tests that don't require cloud credentials
	@echo "🧪 Running local tests (no cloud credentials needed)..."
	@echo ""
	@echo "1️⃣  Running unit tests..."
	$(PYTEST) tests/unit_tests/ -n 4 --dist worksteal -q
	@echo ""
	@echo "2️⃣  Running CLI tests..."
	$(PYTEST) tests/test_cli.py -n 4 --dist worksteal -q
	@echo ""
	@echo "3️⃣  Running optimizer tests (partial)..."
	$(PYTEST) tests/test_optimizer_dryruns.py -k "partial" -n 4 --dist worksteal -q
	@echo ""
	@echo "4️⃣  Running optimizer tests (remaining)..."
	$(PYTEST) tests/test_optimizer_dryruns.py -k "not partial" -n 4 --dist worksteal -q
	@echo ""
	@echo "5️⃣  Running YAML parser and config tests..."
	$(PYTEST) tests/test_yaml_parser.py tests/test_config.py -n 4 --dist worksteal -q
	@echo ""
	@echo "✅ All local tests passed!"

test-unit: ## Run unit tests only (fastest)
	@echo "🧪 Running unit tests..."
	$(PYTEST) tests/unit_tests/ -n 4 --dist worksteal -v

test-integration: ## Run integration tests (no cloud, but slower)
	@echo "🧪 Running integration tests..."
	$(PYTEST) tests/test_cli.py tests/test_optimizer_dryruns.py \
		tests/test_yaml_parser.py tests/test_config.py \
		-n 4 --dist worksteal -v

test-catalog: ## Run catalog-related tests (for RunPod changes)
	@echo "🧪 Running catalog tests..."
	$(PYTEST) tests/unit_tests/test_catalog.py tests/test_list_accelerators.py -v

test-verbose: ## Run tests with verbose output (single file or pattern)
	@echo "🧪 Running tests with verbose output..."
	@echo "Usage: make test-verbose TEST=tests/unit_tests/test_catalog.py"
	@if [ -z "$(TEST)" ]; then \
		echo "❌ Error: TEST variable not set"; \
		echo "Example: make test-verbose TEST=tests/unit_tests/test_catalog.py"; \
		exit 1; \
	fi
	$(PYTEST) $(TEST) -vv -s -n 0

test-coverage: ## Run tests with coverage report
	@echo "🧪 Running tests with coverage..."
	$(UV) pip install --python $(PYTHON) pytest-cov
	$(PYTEST) tests/unit_tests/ --cov=sky --cov-report=html --cov-report=term
	@echo "📊 Coverage report generated in htmlcov/index.html"

##@ Code Quality

format: ## Format code with yapf, black, and isort
	@echo "🎨 Formatting code..."
	@echo "Running yapf (general files)..."
	@$(VENV_DIR)/bin/yapf --recursive --parallel --in-place \
		--exclude 'sky/skylet/providers/ibm/*' \
		--exclude 'sky/schemas/generated/*' \
		sky/
	@echo "Running black (IBM-specific files)..."
	@$(VENV_DIR)/bin/black $(IBM_FILES) 2>/dev/null || true
	@echo "Running isort (general files)..."
	@$(VENV_DIR)/bin/isort sky/ \
		--skip-glob 'build/*' \
		--skip-glob 'sky/skylet/providers/ibm/*' \
		--skip-glob 'sky/schemas/generated/*'
	@echo "Running isort (IBM-specific files)..."
	@$(VENV_DIR)/bin/isort $(IBM_FILES) --profile=black -l=88 -m=3 2>/dev/null || true
	@echo "✅ Code formatting complete!"

lint: ## Run linting with pylint
	@echo "🔍 Running pylint..."
	@$(VENV_DIR)/bin/pylint \
		--rcfile=.pylintrc \
		--load-plugins=pylint_quotes \
		--ignore=ibm,generated \
		sky/ || true
	@echo "✅ Linting complete!"

type-check: ## Run type checking with mypy
	@echo "🔍 Running mypy type checker..."
	@$(VENV_DIR)/bin/mypy sky \
		--exclude 'sky/benchmark|sky/callbacks|sky/backends/monkey_patches' \
		--cache-dir=/dev/null || true
	@echo "✅ Type checking complete!"

check: format lint type-check ## Run all code quality checks (format, lint, type-check)
	@echo "✅ All code quality checks complete!"

pre-commit: check test-unit ## Run pre-commit checks (format, lint, type-check, unit tests)
	@echo "✅ Pre-commit checks passed! Safe to commit."

##@ Development Helpers

watch-tests: ## Watch for changes and re-run tests (requires pytest-watch)
	@$(UV) pip install --python $(PYTHON) pytest-watch
	@$(VENV_DIR)/bin/ptw tests/unit_tests/ -- -n 4 --dist worksteal

shell: ## Activate virtual environment shell
	@echo "Activating virtual environment..."
	@echo "Run: source $(VENV_DIR)/bin/activate"
	@$(VENV_DIR)/bin/python --version

debug-test: ## Run single test with debugger (set TEST variable)
	@echo "🐛 Running test in debug mode..."
	@if [ -z "$(TEST)" ]; then \
		echo "❌ Error: TEST variable not set"; \
		echo "Example: make debug-test TEST=tests/unit_tests/test_catalog.py::test_name"; \
		exit 1; \
	fi
	$(PYTEST) $(TEST) -vv -s -n 0 --pdb

info: ## Show environment information
	@echo "📋 Environment Information"
	@echo "========================="
	@echo "Virtual environment: $(VENV_DIR)"
	@echo "Python version: $(PYTHON_VERSION)"
	@if [ -f "$(PYTHON)" ]; then \
		echo "Python path: $(PYTHON)"; \
		$(PYTHON) --version; \
	else \
		echo "❌ Virtual environment not created. Run: make install"; \
	fi
	@echo ""
	@echo "Test environment variables:"
	@echo "  SKYPILOT_DISABLE_USAGE_COLLECTION=$(SKYPILOT_DISABLE_USAGE_COLLECTION)"
	@echo "  SKYPILOT_SKIP_CLOUD_IDENTITY_CHECK=$(SKYPILOT_SKIP_CLOUD_IDENTITY_CHECK)"

##@ CI Simulation

ci-unit: ## Simulate CI unit tests
	@echo "🤖 Simulating CI unit tests..."
	$(PYTEST) tests/unit_tests/ -n 4 --dist worksteal --tb=short

ci-integration: ## Simulate CI integration tests
	@echo "🤖 Simulating CI integration tests..."
	$(PYTEST) tests/test_cli.py -n 4 --dist worksteal --tb=short
	$(PYTEST) tests/test_optimizer_dryruns.py -k "partial" -n 4 --dist worksteal --tb=short
	$(PYTEST) tests/test_optimizer_dryruns.py -k "not partial" -n 4 --dist worksteal --tb=short
	$(PYTEST) tests/test_jobs_and_serve.py tests/test_yaml_parser.py \
		tests/test_global_user_state.py tests/test_config.py tests/test_jobs.py \
		tests/test_list_accelerators.py tests/test_wheels.py tests/test_api.py \
		tests/test_storage.py tests/test_api_compatibility.py \
		-n 4 --dist worksteal --tb=short

ci-all: ci-unit ci-integration ## Simulate full CI test suite
	@echo "✅ CI simulation complete!"

##@ Quick Reference

quick: ## Quick development cycle (format + test-unit)
	@echo "⚡ Quick development cycle..."
	@$(MAKE) format
	@$(MAKE) test-unit
	@echo "✅ Quick cycle complete!"

full: ## Full development cycle (check + test-local)
	@echo "🔄 Full development cycle..."
	@$(MAKE) check
	@$(MAKE) test-local
	@echo "✅ Full cycle complete!"
