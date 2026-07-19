# Developer shortcuts. `make help` lists targets.
.DEFAULT_GOAL := help
.PHONY: help install lint type test cov check up down worker api migrate seed

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Install runtime + dev dependencies (editable)
	pip install -e ".[dev]"

lint: ## Ruff lint + format check
	ruff check app tests scripts
	ruff format --check app tests scripts

type: ## Static type check
	mypy app

test: ## Run the test suite
	pytest

cov: ## Run tests with coverage gate (>80%)
	pytest --cov=app --cov-report=term-missing --cov-fail-under=80

check: lint type cov ## Run the full CI gate locally (lint + typecheck + coverage)

up: ## Start local stack (postgres, redis, qdrant, ollama, api, worker)
	docker compose up --build

down: ## Stop local stack and remove volumes
	docker compose down -v

migrate: ## Apply database migrations
	alembic upgrade head

seed: ## Load the seed knowledge corpus into Qdrant
	python -m scripts.seed_knowledge

api: ## Run the API locally (expects services up)
	uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

worker: ## Run the async worker locally
	python -m app.jobs.worker
