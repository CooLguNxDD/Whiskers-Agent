.DEFAULT_GOAL := help

.PHONY: help setup doctor env db up migrate test lint typecheck install install-dev

help:
	@echo "Whiskers Agent Server — convenience targets"
	@echo ""
	@echo "  make install     — one-click bootstrap (./install.sh --yes)"
	@echo "  make install-dev — bootstrap with docker-compose.dev.yml"
	@echo "  make setup     — full guided setup (python terminal/script/setup.py all)"
	@echo "  make doctor    — preflight checks (python terminal/script/setup.py doctor)"
	@echo "  make env       — scaffold .env and config (python terminal/script/setup.py env)"
	@echo "  make db        — database bootstrap (python terminal/script/setup.py db)"
	@echo "  make up        — start Docker stack (python terminal/script/setup.py up)"
	@echo "  make migrate   — run Alembic upgrade head (python scripts/migrate.py upgrade head)"
	@echo "  make test      — run backend tests (python scripts/run_tests.py)"
	@echo "  make lint      — ruff check (see pyproject.toml)"
	@echo "  make typecheck — mypy on api/ + core_graph/ (scoped)"

install:
	./install.sh --yes

install-dev:
	./install.sh --yes --dev

setup:
	python terminal/script/setup.py all

doctor:
	python terminal/script/setup.py doctor

env:
	python terminal/script/setup.py env

db:
	python terminal/script/setup.py db

up:
	python terminal/script/setup.py up

migrate:
	python scripts/migrate.py upgrade head

test:
	python scripts/run_tests.py

lint:
	ruff check .

typecheck:
	mypy api core_graph --ignore-missing-imports