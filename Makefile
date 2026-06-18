.PHONY: help
help: ## Show this help message
	@echo "Available targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

.PHONY: skillsaw
skillsaw: ## Run skillsaw linter on skills and plugins
	@echo "Running skillsaw..."
	@if [ -n "$${SKILLSAW_BIN:-}" ]; then \
		"$${SKILLSAW_BIN}"; \
	else \
		uvx skillsaw; \
	fi

.PHONY: skillsaw-fix
skillsaw-fix: ## Auto-fix fixable skillsaw issues
	@echo "Fixing skillsaw issues..."
	@if [ -n "$${SKILLSAW_BIN:-}" ]; then \
		"$${SKILLSAW_BIN}" fix; \
	else \
		uvx skillsaw fix; \
	fi

.PHONY: lint
lint: ## Run skillsaw, ruff, shellcheck, and pytest
	@$(MAKE) skillsaw
	@echo "Running ruff syntax checker on Python scripts..."
	@if command -v ruff >/dev/null 2>&1; then \
		ruff check .; \
	else \
		echo "ruff not found, skipping Python syntax checking. Install with: pip install ruff"; \
		exit 1; \
	fi
	@echo "Running ruff format checker on Python scripts..."
	@ruff format --check --diff .
	@echo "Running shellcheck on shell scripts..."
	@if command -v shellcheck >/dev/null 2>&1; then \
		find . -name '*.sh' -type f -exec shellcheck {} + && echo "All shell checks passed!"; \
	else \
		echo "shellcheck not found, skipping shell script linting. Install with: dnf install ShellCheck"; \
		exit 1; \
	fi
	@$(MAKE) test

.PHONY: test
test: ## Run pytest (excludes integration tests)
	@echo "Running tests..."
	@python3 -m pytest tests/ -v -k "not integration"

.DEFAULT_GOAL := help
