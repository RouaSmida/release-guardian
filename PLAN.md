# Release Guardian — implementation plan

A self-healing release platform: canary rollouts on Kubernetes, analysed with
Prometheus metrics and an anomaly detector, rolled back automatically when bad.

## Stages (each one is committed and pushed when complete)

| # | Stage | Location |
|---|-------|----------|
| 1 | Demo app: `web` (TypeScript), `api` (FastAPI), `worker` (Python) with health, metrics, JSON logs, `FAULT_MODE` | `apps/` |
| 2 | CI: lint, tests, image build, Trivy scan, push to ghcr.io with SHA tags | `.github/workflows/ci.yml` |
| 3 | Delivery: Helm chart, Argo Rollouts canary + AnalysisTemplates, ArgoCD app-of-apps | `deploy/` |
| 4 | Observability + AIOps: kube-prometheus-stack, Loki, dashboards, SLO alerts, `anomaly-detector` | `deploy/observability/`, `apps/anomaly-detector/` |
| 5 | End-to-end proof on kind in GitHub Actions (bad release must be rolled back) | `.github/workflows/e2e.yml`, `scripts/` |
| 6 | Terraform for Azure (AKS, Log Analytics, remote state, GitHub OIDC) — never applied by this repo's automation | `infra/` |
| 7 | Makefile, README, LEARNING, INTERVIEW, RUNBOOK | root, `docs/` |

## Key design decisions

- **One Helm chart** renders all three services from a `services:` map, so every
  service gets the same probes, limits, PDB, HPA, Rollout and analysis.
- **Canary metrics are isolated by pod-template hash** (`rollouts_pod_template_hash`
  label added by a PodMonitor), so analysis judges only the new version.
- **Health endpoints stay green under `FAULT_MODE`** on purpose: the demo shows a
  release that passes probes but is still bad, which only metric analysis catches.
- **Redis** is the job queue between `api` and `worker` (standard, tiny).
- **Same scripts** (`scripts/*.sh`) drive the local demo and the CI end-to-end test.
- **Anomaly detector** uses an Isolation Forest over two features (error rate,
  p95 latency) and publishes `release_anomaly_score` (0 = normal, 1 = anomalous).

## Verification

Local: unit tests and linters. Everything needing a cluster (Helm lint, kind,
Argo Rollouts) and Terraform fmt/validate runs in GitHub Actions.
