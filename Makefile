# Cloud Microservices Architecture & Integration Framework
# Convenience targets for the day-to-day developer loop.
#
#   make up            -> build & start the full stack via docker-compose
#   make down          -> tear down all containers, networks, volumes
#   make logs          -> tail aggregated container logs
#   make ps            -> list running services
#   make test          -> run unit + integration tests with pytest
#   make test-unit     -> unit tests only
#   make test-int      -> integration tests only
#   make health        -> hit every /health endpoint from the host
#   make metrics       -> snapshot every /metrics endpoint from the host
#   make fault-inject  -> stop service-a as a quick chaos demo
#   make orchestrate   -> run the Python orchestrator (health + recovery loop)
#   make collect       -> run the metrics_collector (writes metrics/metrics.json)
#   make aggregate     -> run the log_aggregator (tails docker logs)
#   make clean         -> remove __pycache__, build artefacts, logs

SHELL := /usr/bin/env bash
PYTHON ?= python3
PIP ?= $(PYTHON) -m pip
COMPOSE ?= docker compose

# Load .env so host-side make targets see the same ports as docker-compose.
include .env
export

.DEFAULT_GOAL := help

.PHONY: help up down build rebuild logs ps test test-unit test-int \
        health metrics fault-inject orchestrate collect aggregate clean \
        install-dev lint cpp-prep cpp-clean

help:
	@echo "Cloud Microservices Framework - Make targets"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?##' Makefile | sed -E 's/:.*##/\t/'

up: ## Build and start the full stack
	$(COMPOSE) up -d --build

down: ## Stop and remove containers, networks, volumes
	$(COMPOSE) down -v

build: ## Build all docker images
	$(COMPOSE) build

rebuild: ## Force-rebuild all images with no cache
	$(COMPOSE) build --no-cache

logs: ## Tail aggregated container logs
	$(COMPOSE) logs -f --tail=100

ps: ## List running containers
	$(COMPOSE) ps

install-dev: ## Install host-side dev/test dependencies
	$(PIP) install -r requirements-dev.txt

test: test-unit test-int ## Run all tests

test-unit: ## Run unit tests
	$(PYTHON) -m pytest tests/unit -v

test-int: ## Run integration tests (expects stack to be up)
	$(PYTHON) -m pytest tests/integration -v

health: ## Curl every service's /health endpoint
	@for s in api-gateway:$(API_GATEWAY_PORT) service-a:$(SERVICE_A_PORT) \
	         service-b:$(SERVICE_B_PORT) cpp-worker:$(CPP_WORKER_PORT) \
	         fault-injector:$(FAULT_INJECTOR_PORT); do \
		name=$${s%%:*}; port=$${s##*:}; \
		printf "%-16s -> " "$$name"; \
		curl -fsS http://localhost:$$port/health || echo "DOWN"; \
		echo; \
	done

metrics: ## Curl every service's /metrics endpoint
	@for s in api-gateway:$(API_GATEWAY_PORT) service-a:$(SERVICE_A_PORT) \
	         service-b:$(SERVICE_B_PORT) cpp-worker:$(CPP_WORKER_PORT) \
	         fault-injector:$(FAULT_INJECTOR_PORT); do \
		name=$${s%%:*}; port=$${s##*:}; \
		printf "%-16s -> " "$$name"; \
		curl -fsS http://localhost:$$port/metrics || echo "DOWN"; \
		echo; \
	done

fault-inject: ## Quick chaos demo: stop service-a via fault-injector API
	@curl -fsS -X POST http://localhost:$(FAULT_INJECTOR_PORT)/inject/kill \
	     -H 'content-type: application/json' \
	     -d '{"target": "service-a"}' && echo

orchestrate: ## Run the Python orchestrator (Ctrl+C to stop)
	$(PYTHON) -m orchestration.orchestrator

collect: ## Run the metrics collector loop
	$(PYTHON) -m monitoring.metrics_collector

aggregate: ## Run the docker log aggregator
	$(PYTHON) -m monitoring.log_aggregator

lint: ## Quick syntax/import check across the python tree
	$(PYTHON) -m compileall -q services orchestration monitoring shared tests

cpp-prep: ## Generate compile_commands.json so the IDE resolves cpp-worker includes
	@command -v cmake >/dev/null 2>&1 || { \
		echo "error: cmake not found on PATH"; \
		echo "  macOS: brew install cmake"; \
		echo "  Debian/Ubuntu: sudo apt-get install cmake"; \
		exit 1; \
	}
	@echo "Configuring cpp-worker (this will fetch cpp-httplib and nlohmann/json on first run)..."
	cd services/cpp-worker && cmake -S . -B build \
		-DCMAKE_BUILD_TYPE=Debug \
		-DCMAKE_EXPORT_COMPILE_COMMANDS=ON
	@echo ""
	@echo "compile_commands.json: services/cpp-worker/build/compile_commands.json"
	@echo "Reload your IDE window (or restart clangd) to pick it up."

cpp-clean: ## Remove the cpp-worker local build directory
	rm -rf services/cpp-worker/build

clean: ## Remove caches, logs, build artefacts
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -prune -exec rm -rf {} +
	rm -rf services/cpp-worker/build
	rm -rf logs metrics
