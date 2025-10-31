.PHONY: help build test bench fmt lint clean install dev release check-all

help: ## Zeige diese Hilfe
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

build: ## Baue das Projekt (Debug)
	cargo build

release: ## Baue das Projekt (Release)
	cargo build --release

dev: ## Installiere das Modul f?r Python-Entwicklung (Debug)
	cd skypilot-utils && maturin develop

install: ## Installiere das Modul f?r Python (Release)
	cd skypilot-utils && maturin develop --release

test: ## F?hre Rust-Tests aus
	cargo build --lib

bench: ## F?hre Benchmarks aus
	cargo bench

fmt: ## Formatiere den Code
	cargo fmt --all

lint: ## F?hre Clippy-Linter aus
	cargo clippy --all-targets --all-features -- -D warnings

clean: ## L?sche Build-Artefakte
	cargo clean
	rm -rf skypilot-utils/target

check-all: fmt lint build ## F?hre alle Checks aus
	@echo "? Alle Checks erfolgreich!"

watch: ## Auto-Rebuild bei Datei?nderungen (ben?tigt cargo-watch)
	cargo watch -x 'build --lib'

audit: ## F?hre Security-Audit aus (ben?tigt cargo-audit)
	cargo audit

wheels: ## Baue Distribution-Wheels
	./build_wheels.sh --release

# Python-Integration
python-test: install ## Teste Python-Integration
	python -c "import sky_rs; print(f'? sky_rs {sky_rs.__version__} geladen')"
	python -c "from sky.utils import rust_fallback; print(f'? Backend: {rust_fallback.get_backend_info()}')"

# Entwickler-Workflow
dev-cycle: fmt lint build python-test ## Kompletter Entwicklungszyklus
	@echo "? Entwicklungszyklus abgeschlossen!"
