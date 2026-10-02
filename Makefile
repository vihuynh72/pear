# Pear dev + deploy entry points. `make` (or `make help`) lists every target.
# Every python command runs inside api/ with api/.venv/bin/python and PYTHONDONTWRITEBYTECODE=1.
# Cloud targets need gcloud (brew install --cask google-cloud-sdk); see infra/README.md.
# Debug any target: `make -n <target>` prints the commands without running them.

SHELL := /bin/bash
.DEFAULT_GOAL := help

PROJECT ?= pear-510410
REGION  ?= us-central1
# Not pear-api: that is the live v0.1 demo service. SERVICE=pear-api make deploy replaces it on purpose.
SERVICE ?= pear-api-v3
PORT    ?= 8080
URL     ?= http://localhost:$(PORT)
N       ?= 5
IMAGE   ?= pear-api
GIT_SHA := $(shell git rev-parse --short HEAD 2>/dev/null || echo dev)

API := api
PY  := PYTHONDONTWRITEBYTECODE=1 .venv/bin/python
ADC := $(HOME)/.config/gcloud/application_default_credentials.json

# Fail with a clear message when a script owned by another lane isn't written yet.
need = @test -f $(API)/scripts/$(1).py || { echo "api/scripts/$(1).py does not exist yet"; exit 1; }

.PHONY: help dev-install dev test test-allergens test-live lint kb-export fixtures openapi \
        swift-check docker-build docker-run iam secrets indexes deploy deploy-fast deploy-dry \
        url smoke smoke-mock warm ingest logs tail metrics dev-info

help: ## List targets
	@echo "Pear make targets (vars: URL=$(URL) PROJECT=$(PROJECT) REGION=$(REGION) N=$(N))"
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

# ----------------------------------------------------------------------------- local dev

dev-install: ## Create api/.venv with uv and install requirements-dev.txt
	cd $(API) && uv venv .venv --python 3.13 && uv pip install --python .venv/bin/python -r requirements-dev.txt

dev-info: ## Print LAN IP + local URLs + env summary
	cd $(API) && $(PY) -m scripts.dev_info --port $(PORT)

dev: ## Run the API with reload on 0.0.0.0:PORT (default 8080; loads api/.env)
	cd $(API) && set -a && { [ ! -f .env ] || . ./.env; } && set +a && \
	  $(PY) -m scripts.dev_info --port $(PORT) && \
	  $(PY) -m uvicorn main:app --reload --reload-exclude .venv --host 0.0.0.0 --port $(PORT) --timeout-keep-alive 75

test: ## Run the offline test suite (no network, fake Gemini, memory store)
	cd $(API) && $(PY) -m pytest -m "not live"

test-allergens: ## The no-missed-allergen gate (pytest -m allergen)
	cd $(API) && $(PY) -m pytest -m allergen

test-live: ## Tests that hit real Open Food Facts / Gemini (pytest -m live)
	cd $(API) && $(PY) -m pytest -m live

lint: ## ruff check
	cd $(API) && $(PY) -m ruff check .

kb-export: ## Export kb_index.json (app search) + kb ids (label_reader prompt)
	$(call need,export_kb_index)
	$(call need,export_kb_ids)
	cd $(API) && $(PY) -m scripts.export_kb_index && $(PY) -m scripts.export_kb_ids

fixtures: ## Regenerate contracts/fixtures from demo products
	$(call need,gen_fixtures)
	cd $(API) && $(PY) -m scripts.gen_fixtures

openapi: ## Export contracts/openapi.json
	$(call need,export_openapi)
	cd $(API) && $(PY) -m scripts.export_openapi

swift-check: ## Check contracts/swift against the fixtures
	@test -f contracts/swift/check.sh || { echo "contracts/swift/check.sh does not exist yet"; exit 1; }
	bash contracts/swift/check.sh

# ----------------------------------------------------------------------------- container

docker-build: ## Build the Cloud Run image locally (repo root context)
	docker build --build-arg PEAR_GIT_SHA=$(GIT_SHA) -t $(IMAGE) .

docker-run: ## Run the image on :PORT with the memory store (mounts ADC if present)
	docker run --rm -it -p $(PORT):8080 -e PORT=8080 -e PEAR_STORE=memory \
	  $(if $(wildcard $(ADC)),-v $(ADC):/tmp/adc.json:ro -e GOOGLE_APPLICATION_CREDENTIALS=/tmp/adc.json -e GOOGLE_CLOUD_PROJECT=$(PROJECT),) \
	  $(IMAGE)

# ----------------------------------------------------------------------------- cloud

iam: ## One-time IAM: pear-api service account + roles, run.builder (infra/setup_iam.sh)
	PROJECT=$(PROJECT) bash infra/setup_iam.sh

secrets: ## Create/update the usda-api-key secret from stdin
	PROJECT=$(PROJECT) bash infra/secrets.sh

indexes: ## Verify/create the Firestore vector index
	PROJECT=$(PROJECT) bash infra/firestore/create_indexes.sh

deploy: ## Deploy to Cloud Run (judging settings) and smoke test
	PROJECT=$(PROJECT) REGION=$(REGION) SERVICE=$(SERVICE) bash infra/deploy.sh

deploy-fast: ## Deploy with min-instances 0 (cheap dev iteration)
	PROJECT=$(PROJECT) REGION=$(REGION) SERVICE=$(SERVICE) bash infra/deploy.sh --fast-dev

deploy-dry: ## Print the deploy command without running it
	PROJECT=$(PROJECT) REGION=$(REGION) SERVICE=$(SERVICE) bash infra/deploy.sh --dry-run

url: ## Print the Cloud Run service URL
	@gcloud run services describe $(SERVICE) --project $(PROJECT) --region $(REGION) --format='value(status.url)'

smoke: ## Contract + latency smoke test: make smoke URL=https://... [N=5]
	cd $(API) && $(PY) -m scripts.smoke --url $(URL) --n $(N)

smoke-mock: ## Smoke test only /health and /mock: make smoke-mock URL=...
	cd $(API) && $(PY) -m scripts.smoke --url $(URL) --mock-only

warm: ## Warm product + price caches for the demo: make warm URL=https://...
	$(call need,warm)
	cd $(API) && $(PY) -m scripts.warm --url $(URL)

ingest: ## Ingest Open Food Facts products + embeddings into Firestore (~30 min)
	$(call need,ingest)
	cd $(API) && $(PY) -m scripts.ingest

logs: ## Last 100 Cloud Run log lines
	gcloud run services logs read $(SERVICE) --project $(PROJECT) --region $(REGION) --limit 100

tail: ## Stream Cloud Run logs (needs gcloud beta components)
	gcloud beta run services logs tail $(SERVICE) --project $(PROJECT) --region $(REGION)

metrics: ## Create log-based latency metrics + the "Pear API" dashboard
	PROJECT=$(PROJECT) SERVICE=$(SERVICE) bash infra/monitoring/setup_metrics.sh
