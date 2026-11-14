# Runbook

How to run Release Guardian locally, deploy it to Azure, verify it, and destroy
everything again. Commands are run from the repository root unless stated.

- [1. Run locally (kind)](#1-run-locally-kind)
- [2. Deploy to Azure (AKS)](#2-deploy-to-azure-aks)
- [3. Verify](#3-verify)
- [4. Destroy (avoid costs)](#4-destroy-avoid-costs)
- [5. Troubleshooting](#5-troubleshooting)
- [6. Responding to alerts](#6-responding-to-alerts)

---

## 1. Run locally (kind)

### Prerequisites

| Tool | Used for | Notes |
|------|----------|-------|
| Docker | building images, running kind | about 6 GB of memory for the full profile |
| [kind](https://kind.sigs.k8s.io/) | local Kubernetes cluster | version pinned in `scripts/versions.env` |
| kubectl, Helm 3 | deploying | |
| make, bash | entry points | |
| Node.js 22, Python 3.13 | only for running unit tests outside Docker | |

### Start

```bash
make up
```

This creates a kind cluster named `release-guardian` and installs, in order:
Argo Rollouts, kube-prometheus-stack (Prometheus, Grafana, Alertmanager), Loki
with Alloy, and version `v1` of the application with a load generator.
It finishes when Prometheus reports application traffic.

For a smaller footprint (no Grafana, no Loki — what CI uses):

```bash
make up PROFILE=lean
```

### Demo

```bash
make demo-good
```

Releases a healthy `api` version. The terminal shows the canary moving through
10 % → 50 % → 100 % while every analysis measurement passes.

```bash
make demo-bad
```

Releases an `api` version with `FAULT_MODE=errors` (half of its requests fail,
while its health probes stay green). The script prints the rollout state every
five seconds, then asserts that Argo Rollouts aborted the rollout and that only
the previous version is serving. It exits non-zero if the rollback did not happen.

Variations:

```bash
make demo-bad FAULT_MODE=latency
```

```bash
make demo-bad SERVICE=web
```

### Look around

```bash
make grafana
```

Prints the URL, user and generated password, then port-forwards Grafana to
<http://localhost:3000>. Dashboards are tagged `release-guardian`. Keep
*Canary progress* open in a second window while running a demo.

```bash
make web
```

Port-forwards the demo application to <http://localhost:8080>.

```bash
make status
```

### Stop

```bash
make down
```

---

## 2. Deploy to Azure (AKS)

> **Cost warning.** These steps create billable resources: one virtual machine
> (the AKS node), its disk, a Standard load balancer with a public IP, a storage
> account, and Log Analytics ingestion. The AKS control plane uses the Free tier.
> Check current prices in the Azure pricing calculator for your region before
> applying, and follow [section 4](#4-destroy-avoid-costs) as soon as you are done.
> Nothing in this repository applies Terraform automatically.

### 2.1 Prerequisites

- An Azure subscription where you are **Owner** (role assignments are created).
- Azure CLI, Terraform ≥ 1.9, kubectl, Helm.

```bash
az login
```

```bash
export TF_VAR_subscription_id="$(az account show --query id -o tsv)"
```

The subscription ID is only ever passed through this environment variable; it
is never written to a file in the repository.

### 2.2 Bootstrap (once, local state)

Creates the storage account for the remote Terraform state and the managed
identity that GitHub Actions uses through OIDC.

```bash
cd infra/bootstrap
```

```bash
terraform init
```

```bash
terraform apply
```

Note the outputs (`terraform output`). The bootstrap state stays on your
machine in `infra/bootstrap/terraform.tfstate` (ignored by Git) — keep it, you
need it to destroy the bootstrap later.

### 2.3 GitHub settings

In the repository: **Settings → Secrets and variables → Actions → Variables**.
These are identifiers, not credentials, so they are *variables*, not secrets:

| Variable | Value |
|----------|-------|
| `AZURE_CLIENT_ID` | bootstrap output `github_client_id` |
| `AZURE_TENANT_ID` | bootstrap output `github_tenant_id` |
| `AZURE_SUBSCRIPTION_ID` | your subscription ID |
| `TF_STATE_RESOURCE_GROUP` | bootstrap output `state_resource_group_name` |
| `TF_STATE_STORAGE_ACCOUNT` | bootstrap output `state_storage_account_name` |

Once set, pull requests that touch `infra/` also get a `terraform plan`.

### 2.4 Platform (AKS, Log Analytics)

```bash
cd ../platform
```

```bash
cp backend.hcl.example backend.hcl
```

Edit `backend.hcl` with the bootstrap outputs, then:

```bash
terraform init -backend-config=backend.hcl
```

```bash
terraform plan -out tfplan
```

Review the plan, then:

```bash
terraform apply tfplan
```

If `init` fails with an authorization error on the storage account, wait a
minute: the role assignment from the bootstrap can take a moment to propagate.

Configure kubectl (the exact command is the `get_credentials_command` output):

```bash
az aks get-credentials --resource-group relguard-demo-rg --name relguard-demo-aks
```

### 2.5 Container images

CI pushes images to `ghcr.io/<owner>/release-guardian/<service>:<git-sha>` on
every push to `main` that changes `apps/`. For the cluster to pull them, either
make the four packages public (GitHub → your profile → Packages → package →
Package settings → Change visibility), or create an image pull secret and list
it under `global.imagePullSecrets` in `values-aks.yaml`.

Then set the version to deploy: in
`deploy/charts/release-guardian/values-aks.yaml`, replace
`REPLACE_WITH_GIT_SHA` with the full SHA of a commit whose CI run pushed
images, and commit and push that change.

### 2.6 GitOps with ArgoCD

```bash
source scripts/versions.env
```

```bash
helm repo add argo https://argoproj.github.io/argo-helm
```

```bash
helm upgrade --install argocd argo/argo-cd --version "$ARGOCD_CHART_VERSION" --namespace argocd --create-namespace --wait
```

```bash
kubectl apply -f deploy/argocd/root.yaml
```

From here on, the cluster follows the `main` branch: ArgoCD installs Argo
Rollouts, kube-prometheus-stack, Loki, Alloy and the application. A release is
a commit that changes `values-aks.yaml`.

Note on size: the default node (`Standard_B2s`, 4 GiB) is the cheapest option
and is tight for the full stack. If pods stay `Pending`, set
`node_vm_size = "Standard_B2ms"` in `terraform.tfvars` and apply again.

---

## 3. Verify

```bash
kubectl -n argocd get applications
```

All applications should become `Synced` and `Healthy`.

```bash
kubectl -n release-guardian get rollouts,pods
```

```bash
kubectl -n release-guardian get podmonitor,prometheusrule,analysistemplate
```

Check that Prometheus scrapes the application:

```bash
kubectl -n monitoring port-forward svc/prometheus-operated 9090
```

Open <http://localhost:9090/targets> and look for `release-guardian`. The query
`count by (service, version, rollout_role) (app_info)` shows which version of
each service is running as stable or canary.

There is no load generator on AKS by default (`loadgen.enabled: false`), so
generate some traffic through `kubectl -n release-guardian port-forward svc/web 8080:8080`
or enable the load generator in `values-aks.yaml` before testing a release.

To watch a release:

```bash
kubectl argo rollouts -n release-guardian get rollout api --watch
```

(requires the [Argo Rollouts kubectl plugin](https://argo-rollouts.readthedocs.io/en/stable/installation/#kubectl-plugin-installation)).

---

## 4. Destroy (avoid costs)

Order matters: first remove what ArgoCD deployed, then the platform, then the
bootstrap (its state storage is needed to destroy the platform).

```bash
kubectl delete -f deploy/argocd/root.yaml
```

```bash
cd infra/platform
```

```bash
terraform destroy
```

```bash
cd ../bootstrap
```

```bash
terraform destroy
```

Confirm nothing is left:

```bash
az group list --query "[?starts_with(name, 'relguard')].name" -o tsv
```

AKS also creates a node resource group named `MC_<resource-group>_<cluster>_<region>`;
it is deleted together with the cluster. If the list above is empty and no
`MC_relguard…` group exists, nothing from this project is being billed.
Finally, delete the GitHub Actions variables from section 2.3.

---

## 5. Troubleshooting

| Symptom | Likely cause | What to do |
|---------|--------------|------------|
| `make up` stops at "Prometheus has no http_requests_total data" | PodMonitor not picked up, or pods not ready | `make diagnostics`, then read `artifacts/prometheus-targets.json` and `artifacts/pods.txt` |
| Rollout stays `Paused` | It is in a timed pause between steps | Wait; `make status` shows the step |
| Rollout is `Degraded` after a bad release | The spec still asks for the aborted version | Revert the change (`helm rollback release-guardian -n release-guardian`, or `git revert` with GitOps) |
| AnalysisRun shows `Error` instead of `Failed` | Prometheus unreachable from the controller | Check `prometheus.address` in the chart values and the `monitoring` namespace |
| Anomaly score is always 0 | No canary running, or baseline shorter than `minBaselineSamples` | Check the `anomaly-detector` logs: each cycle logs a `reason` |
| Grafana shows no dashboards | Sidecar has not loaded the ConfigMaps yet, or lean profile | Wait a minute; the lean profile has no Grafana |
| Images cannot be pulled on AKS | ghcr.io packages are private | See section 2.5 |

`make diagnostics` writes rollout status, AnalysisRuns, events, logs and what
Prometheus knows about the pods to `./artifacts`.

---

## 6. Responding to alerts

| Alert | Meaning | First steps |
|-------|---------|-------------|
| `ErrorBudgetFastBurn` (critical) | Errors over the last hour and the last five minutes consume the 99.5 % budget 14.4× too fast | Open the *SLO* dashboard. Check whether a rollout is in progress (*Canary progress*). If a release is the cause and was not aborted, run `kubectl argo rollouts abort <service>` and revert the change. Otherwise check dependencies (Redis) and the error logs in Loki. |
| `ErrorBudgetSlowBurn` (warning) | A slower, sustained burn (6× over six hours) | Not urgent. Look for a steady low error rate on the *Service overview* dashboard and open a ticket. |
| `CanaryAnomalyDetected` (info) | The detector scored a canary above the threshold | Informational: the rollout is aborted automatically. Review the AnalysisRun and the canary's logs before retrying the release. |
