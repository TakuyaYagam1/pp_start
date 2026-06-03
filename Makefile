ifneq ($(wildcard .env),)
ENV_FILE ?= .env
else
ENV_FILE ?= .env.example
endif

PYTHON ?= python
PIP ?= $(PYTHON) -m pip
PYTEST ?= $(PYTHON) -m pytest
RUFF ?= ruff
COMPOSE ?= docker compose
COMPOSE_CMD = $(COMPOSE) --env-file $(ENV_FILE)

.DEFAULT_GOAL := help

# HELP =================================================================================================================

.PHONY: help
help: ## Display this help screen
	@$(PYTHON) scripts/make_help.py $(MAKEFILE_LIST)

# ENV ==================================================================================================================

##@ Environment

.PHONY: env-init
env-init: ## Create .env from .env.example when .env is missing
	@$(PYTHON) -c "from pathlib import Path; env=Path('.env'); src=Path('.env.example'); env.write_text(src.read_text(encoding='utf-8'), encoding='utf-8') if not env.exists() else None; print('.env is ready')"

.PHONY: install
install: ## Install runtime and dev dependencies into current Python environment
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

.PHONY: doctor
doctor: ## Print local tool versions
	@$(PYTHON) --version
	@$(PIP) --version
	@$(RUFF) --version
	@$(COMPOSE) version

# QUALITY ==============================================================================================================

##@ Quality

.PHONY: test
test: ## Run test suite
	$(PYTEST) -q

.PHONY: test-v
test-v: ## Run test suite with verbose output
	$(PYTEST) -ra -v

.PHONY: test-one
test-one: ## Run one test file or node, usage: make test-one path=tests/test_spam_detector.py
ifndef path
	$(error Usage: make test-one path=tests/test_file.py::test_name)
endif
	$(PYTEST) -q $(path)

.PHONY: lint
lint: ## Run Ruff lint checks without cache
	$(RUFF) check . --no-cache

.PHONY: fmt
fmt: ## Format Python code with Ruff
	$(RUFF) format .

.PHONY: fmt-check
fmt-check: ## Check Python formatting
	$(RUFF) format --check .

.PHONY: compile
compile: ## Compile app and tests to catch syntax/import issues
	$(PYTHON) -m compileall app tests

.PHONY: compose-config
compose-config: ## Validate docker compose config using ENV_FILE
	$(COMPOSE_CMD) config --quiet

.PHONY: check
check: lint fmt-check test compile compose-config ## Run all local validation checks

.PHONY: clean
clean: ## Remove local Python/test caches and generated coverage files
	$(PYTHON) -c "from pathlib import Path; import shutil; [shutil.rmtree(p, ignore_errors=True) for root in ('app','tests') for p in Path(root).rglob('__pycache__')]; [shutil.rmtree(p, ignore_errors=True) for p in (Path('.pytest_cache'), Path('.ruff_cache'))]; [p.unlink(missing_ok=True) for p in (Path('coverage.out'), Path('coverage.html'))]"

# COMPOSE ==============================================================================================================

##@ Compose

.PHONY: up
up: env-init ## Build and start full compose stack
	$(COMPOSE_CMD) up -d --build

.PHONY: up-bot
up-bot: env-init ## Rebuild and restart only bot service
	$(COMPOSE_CMD) up -d --build bot

.PHONY: restart-bot
restart-bot: up-bot ## Alias for up-bot

.PHONY: down
down: ## Stop compose stack and remove orphan containers
	$(COMPOSE_CMD) down --remove-orphans

.PHONY: ps
ps: ## Show compose service status
	$(COMPOSE_CMD) ps

.PHONY: logs
logs: ## Follow all compose logs
	$(COMPOSE_CMD) logs -f

.PHONY: logs-bot
logs-bot: ## Follow bot logs
	$(COMPOSE_CMD) logs -f bot

.PHONY: logs-redis
logs-redis: ## Follow Redis logs
	$(COMPOSE_CMD) logs -f redis

.PHONY: shell-bot
shell-bot: ## Open shell inside bot container
	$(COMPOSE_CMD) exec bot sh

.PHONY: redis-cli
redis-cli: ## Open redis-cli inside Redis container
	$(COMPOSE_CMD) exec redis redis-cli

.PHONY: spam-log
spam-log: ## Follow spam.log inside bot container
	$(COMPOSE_CMD) exec bot sh -lc 'tail -f /app/logs/spam.log'

.PHONY: prune
prune: ## Remove stopped compose containers and unused project images
	$(COMPOSE_CMD) down --remove-orphans
	docker image prune -f
