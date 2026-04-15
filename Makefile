.PHONY: all format format-check lint test tests integration_tests help run dev postgres-up postgres-down postgres-logs postgres-ps dreaming-up dreaming-logs dreaming-ps dreaming-reembed

# Default target executed when no arguments are given to make.
all: help

######################
# DEVELOPMENT
######################

dev:
	langgraph dev

run:
	uvicorn agent.webapp:app --reload --port 8000

install:
	uv pip install -e .

postgres-up:
	docker compose -f docker-compose.postgres.yml up -d postgres

postgres-down:
	docker compose -f docker-compose.postgres.yml down

postgres-logs:
	docker compose -f docker-compose.postgres.yml logs -f postgres

postgres-ps:
	docker compose -f docker-compose.postgres.yml ps

dreaming-up:
	docker compose -f docker-compose.postgres.yml up -d postgres repo-memory-dreaming

dreaming-logs:
	docker compose -f docker-compose.postgres.yml logs -f repo-memory-dreaming

dreaming-ps:
	docker compose -f docker-compose.postgres.yml ps postgres repo-memory-dreaming

dreaming-reembed:
	docker compose -f docker-compose.postgres.yml run --rm repo-memory-dreaming /bin/bash -lc "uv sync --frozen --no-dev && uv run repo-memory-dreaming-daemon --reembed-all"

######################
# TESTING
######################

TEST_FILE ?= tests/

test tests:
	@if [ -d "$(TEST_FILE)" ] || [ -f "$(TEST_FILE)" ]; then \
		uv run pytest -vvv $(TEST_FILE); \
	else \
		echo "Skipping tests: path not found: $(TEST_FILE)"; \
	fi

integration_tests:
	@if [ -d "tests/integration_tests/" ] || [ -f "tests/integration_tests/" ]; then \
		uv run pytest -vvv tests/integration_tests/; \
	else \
		echo "Skipping integration tests: path not found: tests/integration_tests/"; \
	fi

######################
# LINTING AND FORMATTING
######################

PYTHON_FILES=.

lint:
	uv run ruff check $(PYTHON_FILES)
	uv run ruff format $(PYTHON_FILES) --diff

format:
	uv run ruff format $(PYTHON_FILES)
	uv run ruff check --fix $(PYTHON_FILES)

format-check:
	uv run ruff format $(PYTHON_FILES) --check

######################
# HELP
######################

help:
	@echo '----'
	@echo 'dev                          - run LangGraph dev server'
	@echo 'run                          - run webhook server'
	@echo 'install                      - install dependencies'
	@echo 'postgres-up                  - start local Postgres + pgvector'
	@echo 'postgres-down                - stop local Postgres + pgvector'
	@echo 'postgres-logs                - follow local Postgres logs'
	@echo 'postgres-ps                  - show local Postgres status'
	@echo 'dreaming-up                  - start local Postgres + Dreaming daemon'
	@echo 'dreaming-logs                - follow Dreaming daemon logs'
	@echo 'dreaming-ps                  - show Dreaming daemon + Postgres status'
	@echo 'dreaming-reembed             - run one full embedding backfill for all repos'
	@echo 'format                       - run code formatters'
	@echo 'lint                         - run linters'
	@echo 'test                         - run unit tests'
	@echo 'integration_tests            - run integration tests'
