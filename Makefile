# Release Guardian — developer entry points. Run `make help` for the list.
SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

PROFILE   ?= full
NAMESPACE ?= release-guardian
CONTEXT   := kind-release-guardian
PY_APPS   := api worker anomaly-detector
export PROFILE NAMESPACE

.PHONY: help up demo-good demo-bad grafana web status diagnostics down test lint tf-check

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

up: ## Create the local kind cluster with everything (PROFILE=lean for Prometheus only)
	@scripts/up.sh

demo-good: ## Release a healthy version: promoted 10% -> 50% -> 100%
	@scripts/release.sh good

demo-bad: ## Release a broken version (FAULT_MODE=errors|latency): rolled back automatically
	@scripts/release.sh bad

grafana: ## Port-forward Grafana and print the URL and credentials
	@echo "Grafana:   http://localhost:3000  (search dashboards by tag 'release-guardian')"
	@echo "User:      admin"
	@echo "Password:  $$(kubectl --context $(CONTEXT) -n monitoring get secret kps-grafana -o jsonpath='{.data.admin-password}' | base64 -d)"
	@echo "Press Ctrl+C to stop."
	@kubectl --context $(CONTEXT) -n monitoring port-forward svc/kps-grafana 3000:80 >/dev/null

web: ## Port-forward the demo application and print the URL
	@echo "Demo application: http://localhost:8080   (Ctrl+C to stop)"
	@kubectl --context $(CONTEXT) -n $(NAMESPACE) port-forward svc/web 8080:8080 >/dev/null

status: ## Show rollouts, analysis runs and pods
	@kubectl --context $(CONTEXT) -n $(NAMESPACE) get rollouts,analysisruns,pods

diagnostics: ## Collect cluster state and logs into ./artifacts
	@scripts/diagnostics.sh

down: ## Delete the local kind cluster
	@scripts/down.sh

test: ## Run all unit tests
	cd apps/web && npm ci && npm test
	@for app in $(PY_APPS); do (cd apps/$$app && python -m pytest -q) || exit 1; done

lint: ## Run all linters
	cd apps/web && npm run lint
	@for app in $(PY_APPS); do (cd apps/$$app && ruff check . && ruff format --check .) || exit 1; done
	helm lint deploy/charts/release-guardian --strict

tf-check: ## terraform fmt and validate (no cloud access needed)
	terraform fmt -check -recursive infra
	@for dir in infra/bootstrap infra/platform; do \
		(cd $$dir && terraform init -backend=false -input=false >/dev/null && terraform validate) || exit 1; done
