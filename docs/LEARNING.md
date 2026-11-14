# Learning guide

This document explains every part of Release Guardian in plain language, layer
by layer: what each component does, which files implement it, why it was built
this way, and which alternatives were considered. Read it top to bottom once;
afterwards use it as a map of the repository.

- [0. The idea in one page](#0-the-idea-in-one-page)
- [1. The demo application](#1-the-demo-application)
- [2. Containers](#2-containers)
- [3. Continuous integration](#3-continuous-integration)
- [4. Kubernetes delivery with Helm](#4-kubernetes-delivery-with-helm)
- [5. Progressive delivery with Argo Rollouts](#5-progressive-delivery-with-argo-rollouts)
- [6. GitOps with ArgoCD](#6-gitops-with-argocd)
- [7. Observability](#7-observability)
- [8. The anomaly detector](#8-the-anomaly-detector)
- [9. The end-to-end proof](#9-the-end-to-end-proof)
- [10. Infrastructure as code](#10-infrastructure-as-code)
- [11. Developer experience](#11-developer-experience)
- [12. Known limitations](#12-known-limitations)
- [Glossary](#glossary)

---

## 0. The idea in one page

Deploying a new version is the most common cause of outages. Kubernetes'
built-in rolling update only asks one question — "does the new pod pass its
health probes?" — and then replaces every old pod. A version that starts fine
but returns errors on half of its requests passes that check and takes the
whole service down.

Release Guardian adds two things:

1. **A canary**: the new version first receives only a small share of traffic.
2. **A judge**: while the canary runs, its real behaviour (error rate, latency,
   and an anomaly score) is measured. If it misbehaves, the release is aborted
   and traffic returns to the old version — without a human.

```
new version ──► 10 % of pods ──► measure ──► 50 % ──► measure ──► 100 %
                                   │                     │
                                   └── bad? abort ◄──────┘   (old version keeps serving)
```

The layers, from bottom to top:

| Layer | Tool | Folder |
|-------|------|--------|
| Cloud infrastructure | Terraform (Azure: AKS) | `infra/` |
| Cluster state from Git | ArgoCD | `deploy/argocd/` |
| Packaging | Helm | `deploy/charts/release-guardian/` |
| Progressive delivery | Argo Rollouts | chart templates `rollout.yaml`, `analysistemplates.yaml` |
| Metrics, logs, dashboards, alerts | Prometheus, Loki, Grafana | `deploy/observability/`, chart `dashboards/` |
| AIOps | anomaly detector (Isolation Forest) | `apps/anomaly-detector/` |
| Application | web, api, worker | `apps/` |
| Automation | GitHub Actions, Makefile, scripts | `.github/workflows/`, `Makefile`, `scripts/` |

---

## 1. The demo application

The application is deliberately small: the platform is the subject, the app
only has to produce realistic signals (requests, errors, latency, logs).

```
browser ──► web (TypeScript) ──► api (FastAPI) ──► Redis queue ──► worker (Python)
```

A user submits a piece of text; the API stores it as a *job* and queues it; the
worker picks it up, computes simple statistics (word count, unique words,
hash) and stores the result; the page shows recent jobs.

### What every service has in common

| Feature | Why |
|---------|-----|
| `GET /healthz` | **Liveness**: "is the process alive?" Kubernetes restarts the container if this fails. It checks nothing external, otherwise a Redis outage would restart healthy pods. |
| `GET /readyz` | **Readiness**: "can I serve traffic right now?" Kubernetes removes the pod from the Service while this fails. The API and worker check Redis here. |
| `GET /metrics` | Prometheus text format: a request counter, an error counter and a latency histogram. |
| JSON logs on stdout | One JSON object per line, so Loki can filter on fields (`level`, `status`, `service`, `version`). |
| `FAULT_MODE` | `none`, `errors` (half of the requests fail) or `latency` (requests are delayed). Lets a release be broken on purpose. |

Two details matter for the whole project:

- **Faults never touch the health endpoints.** A broken release still passes
  its probes. That is exactly the kind of failure a plain rolling update
  cannot see and metric analysis can.
- **Health endpoints are not counted in the metrics.** Probes hit them every
  few seconds; counting them would dilute the error rate.

### `apps/api` — FastAPI

| File | Role |
|------|------|
| `app/main.py` | `create_app()` builds the application: one middleware measures every request and injects faults; routes for health, metrics, and jobs. `main()` starts uvicorn. |
| `app/config.py` | Reads environment variables once into an immutable `Settings` object and rejects an invalid `FAULT_MODE` at startup (fail fast). |
| `app/store.py` | `RedisStore` (used in the cluster) and `InMemoryStore` (tests, local runs) behind the same interface. |
| `app/logging_config.py` | A small JSON formatter for Python's standard `logging`. |
| `tests/test_api.py` | Unit tests through FastAPI's test client: job lifecycle, validation, metrics, both fault modes, readiness failing while liveness stays green. |

Design choices:

- *Application factory* (`create_app(settings, store, rng)`): dependencies are
  passed in, so tests inject an in-memory store and a seeded random generator.
  No global state, no monkey-patching.
- *Route template as metric label* (`/api/jobs/{job_id}`, not
  `/api/jobs/abc123`): every distinct label value creates a new time series in
  Prometheus. Raw paths would create unbounded series ("cardinality explosion").
- *Explicit error counter* in addition to the `status` label: the canary query
  becomes a simple division, and it is easy to explain.

### `apps/worker` — background jobs

| File | Role |
|------|------|
| `worker/main.py` | The loop: wait for a job, process it, repeat. Handles `SIGTERM` so a job in progress finishes before the pod stops. |
| `worker/processor.py` | `analyse_text()` (the work), plus metrics and fault injection in `Processor.process()`. |
| `worker/store.py` | Queue access (`BLPOP` on a Redis list) with an in-memory twin for tests. |
| `worker/health.py` | A tiny HTTP server from the standard library for `/healthz`, `/readyz`, `/metrics`. |

The worker has no HTTP traffic, so its "request" is a job:
`worker_jobs_total`, `worker_job_errors_total`, `worker_job_duration_seconds`.
The Helm values map these names so the same analysis template works for it.

*Why Redis and not a database table or RabbitMQ/Kafka?* A Redis list is the
simplest real queue: one command to push, one blocking command to pop. Kafka
or RabbitMQ would add operational weight without teaching anything about
releases. The trade-off: this queue is "at most once" — a job taken by a
worker that crashes is lost. For demo data that is acceptable; a real system
would use acknowledgements (Redis Streams, RabbitMQ, a cloud queue).

### `apps/web` — TypeScript / Express

| File | Role |
|------|------|
| `src/app.ts` | `createApp()`: health and metrics routes, one middleware for metrics/logging/faults, a small reverse proxy for `/api/*`, static files. |
| `src/config.ts` | Typed configuration from environment variables. |
| `src/server.ts` | Starts the server and shuts down gracefully on `SIGTERM`. |
| `public/` | The UI: plain HTML, CSS and JavaScript — no framework, no build step. |
| `test/app.test.ts` | Tests with Node's built-in test runner against a stand-in API server. |

Design choices:

- *The browser only talks to `web`*, which forwards `/api/*` to the API.
  One origin (no CORS), and the API is not exposed outside the cluster.
- *The proxy opens a new connection per request.* With keep-alive, `web` would
  stay connected to the same API pod and a canary pod might never receive
  traffic. A new connection lets the Kubernetes Service choose a pod each time.
  This costs a little latency and is called out as a demo simplification; a
  service mesh or ingress-level traffic split is the production answer.
- *`web` readiness does not check the API.* If it did, an API outage would
  mark every `web` pod unready and turn a partial failure into a total one
  (a cascading failure).
- *The UI shows the outcome of its own requests* as a strip of green and red
  squares. During a broken canary you can see a share of requests fail and
  then recover after the rollback.

### `apps/anomaly-detector`

Covered in [section 8](#8-the-anomaly-detector).

---

## 2. Containers

Each service has a `Dockerfile` with the same three ideas:

1. **Multi-stage build.** The first stage installs dependencies (and compiles
   TypeScript); the final stage copies only the result. Compilers, dev
   dependencies and caches never reach the final image, which makes it smaller
   and leaves less for an attacker or a vulnerability scanner to find.
2. **Non-root user.** The process runs as an unprivileged numeric user
   (`USER 10001` / `USER 1000`). Numeric, because Kubernetes'
   `runAsNonRoot: true` can only verify a numeric UID.
3. **Small base image** (`python:3.13-slim`, `node:22-alpine`).

`.dockerignore` keeps tests, caches and `node_modules` out of the build context.

*Alternatives considered:* distroless images are smaller still, but have no
shell, which makes learning and debugging harder. Base images are pinned to a
minor version (`3.13-slim`) rather than a digest: patch updates with security
fixes arrive on rebuild, and Dependabot proposes minor/major bumps. Pinning by
digest is stricter and a reasonable next step.

---

## 3. Continuous integration

File: `.github/workflows/ci.yml`. Runs on every push and pull request that
touches `apps/`.

| Job | What it does |
|-----|--------------|
| `web` | `npm ci`, ESLint + TypeScript type check, unit tests, build |
| `python` (matrix: api, worker, anomaly-detector) | install, `ruff check`, `ruff format --check`, `pytest` |
| `images` (matrix: four services) | build the image, scan it with Trivy, push it to ghcr.io (only on `main`) |

Things worth understanding:

- **`needs: [web, python]`** — images are only built when lint and tests pass.
- **Trivy** scans the built image for known vulnerabilities and fails the job
  on *critical* ones that have a fix available (`ignore-unfixed: true`,
  otherwise the build would be blocked by issues nobody can fix yet).
- **Immutable tags.** Images are tagged with the git commit SHA, never
  `latest`. A SHA tag always means the same code, so a rollback is exact and
  Kubernetes never serves a stale cached `latest`.
- **Least privilege.** The workflow token is read-only by default; only the
  image job gets `packages: write`. The registry login uses the built-in
  `GITHUB_TOKEN` — no personal access token is stored.
- **Pinned versions.** Actions are pinned to exact release tags, and
  `.github/dependabot.yml` proposes updates (grouped: one pull request per
  ecosystem). Pinning to a full commit SHA is stricter still.
- **`concurrency`** cancels an older run when a newer commit arrives.

Other workflows: `chart.yml` (Helm lint and render), `e2e.yml`
([section 9](#9-the-end-to-end-proof)), `terraform.yml`
([section 10](#10-infrastructure-as-code)).

---

## 4. Kubernetes delivery with Helm

Folder: `deploy/charts/release-guardian/`.

Helm is a templating and packaging tool: templates + values = Kubernetes
manifests. This chart renders the three services from one `services:` map in
`values.yaml`, so each of them automatically gets the same set of objects.

| Template | Kubernetes object | Purpose |
|----------|-------------------|---------|
| `rollout.yaml` | Rollout (Argo) | Runs the pods; replaces a Deployment and adds the canary strategy |
| `service.yaml` | Service | Stable network name (`web`, `api`, `worker`) in front of the pods |
| `pdb.yaml` | PodDisruptionBudget | "Keep at least one pod during node maintenance" |
| `hpa.yaml` | HorizontalPodAutoscaler | Scales the Rollout on CPU (enabled in `values-aks.yaml`) |
| `podmonitor.yaml` | PodMonitor | Tells Prometheus to scrape the pods |
| `analysistemplates.yaml` | AnalysisTemplate (Argo) | The PromQL checks a canary must pass |
| `anomaly-detector.yaml` | Deployment | The anomaly detector |
| `redis.yaml` | Deployment + Service | The job queue |
| `loadgen.yaml` | Deployment | Synthetic traffic (demo clusters only) |
| `prometheusrule.yaml` | PrometheusRule | SLO recording rules and alerts |
| `dashboards.yaml` + `dashboards/*.json` | ConfigMaps | Grafana dashboards |
| `serviceaccount.yaml` | ServiceAccount | Identity of the pods, without an API token |
| `_helpers.tpl` | — | Shared snippets: labels, image name, security context |

Values files: `values.yaml` (defaults, documented), `values-kind.yaml` (short
pauses, load generator on), `values-aks.yaml` (ghcr.io images, autoscaling on).

### Settings every pod gets, and why

- **Resource requests and limits.** The *request* is what the scheduler
  reserves; without it, pods are packed blindly and the HPA cannot compute a
  CPU percentage. The *limit* caps a runaway process.
- **Three probes.** `startupProbe` gives a slow start time without loosening
  the others; `livenessProbe` restarts a hung process; `readinessProbe`
  controls whether the pod receives traffic.
- **Security context.** Non-root, read-only root filesystem, all Linux
  capabilities dropped, no privilege escalation, default seccomp profile.
  These are the settings the Kubernetes "restricted" Pod Security Standard asks for.
- **`maxUnavailable: 0`.** During a rollout a new pod must be ready before an
  old one is removed, so capacity never drops.

### Alternatives considered

- *One chart per service* or a generic "library" chart: more reusable across
  teams, but three near-identical charts are noise in a project this size.
- *Kustomize*: fine for overlays, but the loop over services and the values
  files are simpler to express in Helm, and ArgoCD supports both.
- *Plain Deployment + Service*: no canary, no analysis — the point of the project.

---

## 5. Progressive delivery with Argo Rollouts

Argo Rollouts is a Kubernetes controller that adds a `Rollout` object: like a
Deployment, plus a release *strategy*.

### The strategy (`templates/rollout.yaml`)

```yaml
steps:
  - setWeight: 10      # start the canary
  - pause: {duration: 60s}
  - setWeight: 50
  - pause: {duration: 60s}
  # end of steps = promote to 100 %
analysis:              # background analysis
  startingStep: 1      # begins once the canary is up
  templates: [api-health, api-anomaly]
```

While the steps advance, the *background analysis* runs in parallel. It takes
a measurement every interval. If a metric fails more often than `failureLimit`
allows, the Rollout is **aborted**: the canary ReplicaSet is scaled to zero and
the stable ReplicaSet — which was never removed — serves all traffic. That is
the automatic rollback.

### The checks (`templates/analysistemplates.yaml`)

For each service, `<service>-health` contains two metrics:

- **error-rate**: errors ÷ requests over the last minute must be `< 0.02` (2 %).
- **p95-latency**: `histogram_quantile(0.95, …)` must be `< 0.5` (500 ms).
  The 95th percentile means "95 % of requests were faster than this"; it shows
  slowness that an average hides.

`<service>-anomaly` checks `release_anomaly_score < 0.7`
([section 8](#8-the-anomaly-detector)).

How does a query look only at the canary? The PodMonitor copies the pod label
`rollouts-pod-template-hash` onto every metric. That hash identifies the
ReplicaSet, i.e. the version. The Rollout passes the canary's hash to the
template as the argument `canary-hash`, and the query filters on it.

### Design decisions and their trade-offs

- **Canary instead of blue-green.** Blue-green runs a full second copy and
  switches all traffic at once: simple, but a bad version hits every user at
  the switch (or needs a separate pre-production test). A canary limits the
  blast radius and judges the version on real traffic.
- **Background analysis instead of analysis steps.** Inline analysis steps
  only measure at fixed points; background analysis watches the whole rollout
  and can abort at any moment.
- **Weights without a traffic router.** With no ingress controller or service
  mesh configured, Argo Rollouts approximates the weight by pod count: with
  two replicas, "10 %" is one canary pod next to two stable pods, so roughly a
  third of the requests. Exact percentages need a traffic router (NGINX
  Ingress, Gateway API, Istio…). That was left out to keep the cluster light;
  the analysis is unaffected because it only looks at the canary's own pods.
- **No data counts as a pass.** Each query ends with `or on() vector(0)`:
  if the canary received no requests, the result is 0 instead of "no data",
  which Argo would treat as an error. This is a *fail-open* choice that suits
  a demo with a load generator. In production, prefer to fail closed: require
  a minimum request rate, or treat missing data as inconclusive, so a canary
  that receives no traffic cannot be promoted unjudged.
- **`failureLimit: 1`.** One bad measurement is tolerated, the second aborts.
  This avoids aborting on a single noisy sample at the cost of a few seconds.
- **After an abort the Rollout is `Degraded`.** Traffic is safe, but the spec
  still asks for the bad version. The fix is to revert the change (in GitOps:
  `git revert`). The demo script does this with `helm rollback`.

The labels `rollout-role: canary|stable` (`canaryMetadata` / `stableMetadata`)
are maintained on the pods by Argo Rollouts and end up as the Prometheus label
`rollout_role`. Dashboards and the anomaly detector use it to compare the two.

---

## 6. GitOps with ArgoCD

GitOps means: the desired state of the cluster lives in Git, and a controller
inside the cluster continuously makes reality match it. Nobody runs
`kubectl apply` or `helm upgrade` by hand; a release is a commit.

Folder: `deploy/argocd/`.

| File | Role |
|------|------|
| `root.yaml` | The "app of apps". The only manifest applied by hand. It tells ArgoCD to deploy everything in `apps/`. |
| `apps/project.yaml` | An AppProject: restricts which repositories and destinations the applications may use. |
| `apps/argo-rollouts.yaml` | Installs Argo Rollouts from its Helm chart (pinned version). |
| `apps/kube-prometheus-stack.yaml` | Installs Prometheus/Grafana; chart from upstream, values from this repository (two sources). |
| `apps/loki.yaml` | Loki and Alloy. |
| `apps/release-guardian.yaml` | The application chart with `values-aks.yaml`. |

Details:

- **Sync waves** (`argocd.argoproj.io/sync-wave`) order the installation:
  operators and their CRDs first, the application last.
- **`automated: {prune, selfHeal}`**: removed-from-Git means removed from the
  cluster, and manual changes in the cluster are reverted.
- ArgoCD and Argo Rollouts are separate tools with separate jobs: ArgoCD
  decides *what* should run (from Git); Argo Rollouts decides *how* a change
  is rolled out.

*Pull versus push.* The alternative is a CI job that runs `helm upgrade`
against the cluster (push). Pull-based GitOps needs no cluster credentials in
CI, shows drift, and gives every change a commit as its audit trail. Flux is
the main alternative to ArgoCD; ArgoCD was chosen for its UI, which helps when
learning, and for the app-of-apps pattern.

*What the local demo does differently:* `make demo-good` / `make demo-bad` call
`helm upgrade` directly so the demo needs no Git push and no ArgoCD. With
ArgoCD the same release is a commit that changes `values-aks.yaml`.
The ArgoCD layout is not exercised by CI.

---

## 7. Observability

"Observability" here means three kinds of signals: **metrics** (numbers over
time), **logs** (events), and what is built on them: **dashboards** and **alerts**.

### Metrics: Prometheus

`deploy/observability/kube-prometheus-stack/values.yaml` configures the
kube-prometheus-stack chart, which installs the *Prometheus Operator*. The
operator lets you describe monitoring as Kubernetes objects:

- a **PodMonitor** (`templates/podmonitor.yaml`) says "scrape `/metrics` on the
  port named `http` of every pod labelled `part-of: release-guardian`";
- a **PrometheusRule** (`templates/prometheusrule.yaml`) holds recording and
  alerting rules.

The setting `…SelectorNilUsesHelmValues: false` makes Prometheus pick these up
from every namespace, so the application ships its own monitoring next to its
own code. `values-lean.yaml` is the same with Grafana, Alertmanager and the
node/cluster exporters switched off — used by CI.

*Prometheus pulls* metrics from the pods rather than the pods pushing them.
A pod that disappears simply stops being scraped, and the application needs no
knowledge of where the monitoring system lives.

*Counters, not rates.* The applications expose ever-growing counters;
Prometheus computes rates with `rate(counter[1m])`. *Histograms* count requests
per latency bucket, which is what makes percentiles across many pods possible.

### Logs: Loki

`deploy/observability/loki/values.yaml` runs Loki as a single pod with a
volume — the smallest mode. `alloy-values.yaml` configures Grafana Alloy to
read every pod's log through the Kubernetes API and push it to Loki with
`namespace`, `pod`, `container` and `app` labels. Because the applications log
JSON, a query such as `{namespace="release-guardian"} | json | level="error"`
works without any parsing rules.

Loki indexes only labels, not the log text, which makes it cheap to run;
Elasticsearch indexes everything and is heavier. Loki and Alloy are installed
by `make up` and by ArgoCD, but not by the lean CI profile.

### Dashboards as code

`deploy/charts/release-guardian/dashboards/*.json` are loaded into Grafana by
a sidecar that watches for ConfigMaps labelled `grafana_dashboard: "1"`.
Dashboards therefore go through Git and code review like everything else.

| Dashboard | Shows |
|-----------|-------|
| Service overview | Rate, errors, duration (the "RED" method) per service; worker jobs; error logs |
| SLO and error budget | Availability against 99.5 %, remaining budget, burn rate |
| Canary progress | Canary versus stable: traffic share, error rate, p95, anomaly score with the abort thresholds |

### SLO and burn-rate alerts

An **SLO** (service level objective) is a target: *99.5 % of HTTP requests
succeed*. The **error budget** is what remains: 0.5 % may fail. Alerting on
the budget, rather than on "any error", pages only when users are meaningfully
affected.

**Burn rate** is how fast the budget is being spent. Burn rate 1 uses exactly
the budget over the period; 14.4 would use a 30-day budget in two days.
`prometheusrule.yaml` implements the multi-window, multi-burn-rate pattern
from the Google SRE Workbook:

| Alert | Condition | Meaning |
|-------|-----------|---------|
| `ErrorBudgetFastBurn` (critical) | burn > 14.4 over 1 h **and** over 5 m | Severe and still happening → page |
| `ErrorBudgetSlowBurn` (warning) | burn > 6 over 6 h **and** over 30 m | Slow leak → ticket |

The long window proves the problem is significant; the short window proves it
is still happening, so the alert clears quickly after recovery.

---

## 8. The anomaly detector

Folder: `apps/anomaly-detector/`.

### Why, when there are already thresholds?

Fixed thresholds (2 %, 500 ms) are clear but blunt. A canary whose p95 goes
from 20 ms to 300 ms, or whose error rate goes from 0 % to 1.5 %, passes both
— yet it clearly behaves differently from the version it replaces. The
detector asks a relative question: *does the canary look like the stable
version's recent behaviour?*

### How it works

Every 30 seconds, for each service (`detector/main.py`):

1. Ask Prometheus for the **canary's** current error rate and p95 latency
   (`rollout_role="canary"`). No canary → score 0.
2. Ask for the **stable** version's values over the last 30 minutes — the
   baseline. Too few samples → score 0 (reason is logged).
3. Compute the score (`detector/scoring.py`) and publish it as
   `release_anomaly_score{service="…"}`.

The score comes from an **Isolation Forest**. The intuition: describe each
moment as a point (error rate, p95). Build many random trees that keep
splitting the points until each one is alone. A point that sits far from the
rest gets separated after very few splits. The average number of splits
becomes a score: about 0.5 or less means "ordinary", towards 1 means "easy to
isolate, therefore unusual".

`scoring.py` is about fifteen lines and makes three choices, each commented in
the code:

1. **Jitter and fixed-size resampling of the baseline.** A healthy baseline is
   often perfectly flat (0 % errors). Without any width, a canary with 0.1 %
   errors would look infinitely unusual. Small random noise — 0.5 percentage
   points of error rate, 20 ms of latency — defines "differences below this do
   not matter". Resampling to a fixed number of points makes the score mean the
   same thing for short and long baselines.
2. **Only "worse" counts.** The canary's values are raised to at least the
   baseline median, so a faster or cleaner canary is never flagged.
3. **The canary point is included when fitting.** Isolation trees only split
   inside the range of the data they were built on, so a point far outside
   that range can only be isolated if it is part of the data. The forest is
   therefore fitted on the stable baseline plus the one canary observation
   being judged.

The random seed is fixed, so the same inputs always give the same score.

### How it is used

`<service>-anomaly` in `analysistemplates.yaml` fails the rollout when the
score reaches 0.7. In the unit tests (`tests/test_detector.py`), with a flat
baseline: a canary within the noise scores about 0.4; one with a 2 % error
rate about 0.7; one with 50 % errors about 0.84.

### Honest limitations

- Two features and a short baseline: this is an explainable illustration of
  the technique, not a tuned production model. It has not been evaluated on
  real incident data.
- It adds a delay: the detector runs every 30 s on top of the scrape interval,
  so for a grossly broken release the plain error-rate check fires first. The
  detector's value is the subtle case between "normal" and "over the threshold".
- No seasonality: a baseline from a quiet period judges a canary in a busy one.
- Simpler alternatives exist and are worth knowing: compare canary and stable
  directly (ratio or difference), a z-score against the baseline, or Argo
  Rollouts' Kayenta-style comparisons. The Isolation Forest was chosen because
  it handles several metrics at once without choosing a threshold per metric,
  and stays small enough to read.

---

## 9. The end-to-end proof

Files: `.github/workflows/e2e.yml`, `scripts/`.

Unit tests prove the parts; this workflow proves the promise. On a GitHub
runner it:

1. creates a kind cluster (Kubernetes inside Docker);
2. builds the four images and loads them into the cluster;
3. installs Argo Rollouts and the lean Prometheus profile;
4. deploys version 1 and waits until Prometheus reports traffic;
5. releases a version with `FAULT_MODE=errors`;
6. **asserts** that the rollout is aborted, that the canary pods are gone and
   that every running pod serves the old version;
7. uploads rollout status, AnalysisRuns, events and logs as artifacts.

It runs on pushes to `main` that touch the app, chart or scripts, and can be
started manually with a choice of fault mode.

| Script | Role |
|--------|------|
| `scripts/versions.env` | The pinned versions of kind, Argo Rollouts and the Helm charts |
| `scripts/lib.sh` | Shared settings and helpers (colours, `kubectl`/`helm` with a fixed context) |
| `scripts/up.sh` | Steps 1–4 (`make up`) |
| `scripts/release.sh` | Steps 5–6 (`make demo-good`, `make demo-bad`); exits non-zero when the expectation is not met |
| `scripts/diagnostics.sh` | Step 7 (`make diagnostics`) |
| `scripts/down.sh` | Deletes the cluster |

The important design choice: **CI and the local demo run the same scripts.**
There is no separate "test version" of the deployment that could drift from
what a person runs on their laptop.

The broken release is the same image with a different `FAULT_MODE` and version
label. From Kubernetes' point of view that is a new pod template — a real
rollout — without having to build a second image.

---

## 10. Infrastructure as code

Folder: `infra/`. Terraform describes cloud resources in files; `plan` shows
what would change, `apply` makes the change. **This repository never applies
it for you.**

### `infra/bootstrap` — run once, by hand

Terraform needs somewhere to keep its *state* (its record of what it created),
and CI needs an identity. Both must exist before CI can manage anything —
the chicken-and-egg problem this folder solves.

| Resource | Purpose |
|----------|---------|
| Storage account + container | Remote state for `infra/platform`: shared, versioned, lockable |
| User-assigned managed identity | The identity GitHub Actions assumes |
| Federated identity credentials | Trust rules: "accept GitHub's OIDC tokens for this repository's `main` branch and pull requests" |
| Role assignments | What that identity (and you) may do |

**OIDC federation** is the key idea. Instead of storing a long-lived client
secret in GitHub, the workflow asks GitHub for a short-lived signed token
saying "I am a run of repository X on branch Y". Azure is configured to trust
exactly that claim and exchanges it for an access token. Nothing secret is
stored, nothing needs rotating, and a leaked workflow log contains no credential.

### `infra/platform` — the cluster

| Resource | Notes |
|----------|-------|
| Resource group | Container for everything; deleting it deletes everything |
| Log Analytics workspace | Azure's log store, with a daily ingestion cap as a cost guard |
| AKS cluster | `sku_tier = "Free"` control plane, one small node, managed identity, OIDC issuer enabled |

Files follow the usual convention: `versions.tf` (Terraform, providers,
backend), `variables.tf` (inputs with descriptions and validation),
`main.tf` (resources), `outputs.tf`. The subscription ID is an input variable
supplied through `TF_VAR_subscription_id`; it never appears in the repository.
The backend's account details are passed at `init` time (`backend.hcl`), for
the same reason.

### `.github/workflows/terraform.yml`

`fmt -check`, `validate` and `tflint` run without any cloud access. A `plan`
job runs on pull requests only when the Azure variables are configured, and
authenticates through OIDC. There is no apply job.

### Trade-offs

- *One node, Free tier* is the cheapest working cluster, not a resilient one:
  no SLA on the control plane, no redundancy. Fine for a demo.
- *Contributor on the subscription* for the CI identity is broad. It is needed
  because Terraform creates the resource group. To narrow it, create the group
  in the bootstrap and scope the role to that group.
- *Azure CNI Overlay* gives pods their own address range without consuming
  virtual-network addresses; kubenet is being retired.
- *Bicep* is Azure's own IaC language; Terraform was chosen because it is
  cloud-neutral and the most commonly requested.

---

## 11. Developer experience

`Makefile` is the single entry point; `make help` lists the targets.

| Target | Does |
|--------|------|
| `make up` | Local cluster with everything (`PROFILE=lean` for less) |
| `make demo-good` / `make demo-bad` | The two release demos |
| `make grafana` / `make web` | Port-forward and print the URL |
| `make status` / `make diagnostics` | Inspect |
| `make test` / `make lint` / `make tf-check` | Checks |
| `make down` | Delete the cluster |

The Makefile only delegates to `scripts/`, so the logic stays in readable,
individually runnable shell scripts.

---

## 12. Known limitations

Stated plainly, because knowing the limits is part of understanding a design:

- Canary weights are approximated by pod count (no traffic router).
- Missing metrics are treated as a pass (fail-open), see [section 5](#5-progressive-delivery-with-argo-rollouts).
- The broken release is simulated with a flag, not a real defect.
- Redis is a single, non-persistent pod; the queue is at-most-once.
- The anomaly detector is a small illustrative model, not validated on real incidents.
- The ArgoCD layout, the Loki/Alloy values and the Terraform code are provided
  and linted, but are not exercised by the CI end-to-end test, and Terraform
  has not been applied from this repository.
- No ingress, TLS, network policies, or secret management — out of scope.

---

## Glossary

| Term | Meaning |
|------|---------|
| Canary | A new version that receives a small share of traffic first |
| Stable | The version currently trusted with all traffic |
| ReplicaSet | The Kubernetes object that keeps N identical pods running; one per version |
| Pod template hash | Label that identifies which ReplicaSet (version) a pod belongs to |
| AnalysisTemplate / AnalysisRun | The definition of a check / one execution of it |
| CRD | Custom Resource Definition: how tools add new object types (Rollout, PodMonitor) to Kubernetes |
| Operator | A controller that manages software through CRDs |
| PromQL | Prometheus' query language |
| p95 | 95th percentile: 95 % of requests were faster than this value |
| SLI / SLO | The measurement (share of successful requests) / the target for it (99.5 %) |
| Error budget | The allowed amount of failure: 100 % − SLO |
| Burn rate | How fast the error budget is being used; 1 = exactly on budget |
| GitOps | Operating a system by changing Git; a controller applies the changes |
| OIDC federation | Trusting short-lived identity tokens from another system instead of storing a secret |
| Remote state | Terraform's record of managed resources, stored in shared storage instead of a laptop |
