# Interview questions

Twenty questions an interviewer is likely to ask about this project, with
short, honest answers. Where the project has a limitation, the answer says so —
knowing the limits is a better signal than pretending there are none.

---

### 1. What problem does this project solve?

Deployments are a leading cause of incidents, and a Kubernetes rolling update
only checks that new pods pass their probes. A version can pass its probes and
still fail real requests. Release Guardian rolls a new version out as a
canary, measures its real behaviour with Prometheus, and aborts the release
automatically when it misbehaves, so the old version keeps serving.

### 2. Walk me through what happens when a bad release is deployed.

The pod template changes, so Argo Rollouts starts a canary ReplicaSet next to
the stable one and begins a background analysis. Every interval it runs PromQL
queries against the canary's pods only: error rate, p95 latency and the
anomaly score. With `FAULT_MODE=errors` the error rate is far above 2 %; after
the second failed measurement the AnalysisRun is `Failed`, the Rollout is
aborted, the canary is scaled to zero and the untouched stable ReplicaSet
serves everything. In the CI end-to-end run this took about 40 seconds from
the release to the abort.

### 3. Why a canary and not blue-green or a rolling update?

A rolling update has no judgement beyond probes. Blue-green switches all
traffic at once, so a bad version reaches every user at the switch, and it
needs double the capacity. A canary exposes a small share of traffic first and
judges the version on real requests. The cost is a slower rollout and the need
for good metrics.

### 4. How do the analysis queries isolate the canary from the stable version?

Every pod carries the label `rollouts-pod-template-hash`, which identifies its
ReplicaSet. The PodMonitor copies that label onto every scraped metric
(`podTargetLabels`). The Rollout passes the canary's hash into the
AnalysisTemplate as an argument, and the query filters on it. So the canary is
judged on its own error rate, not on a diluted average.

### 5. Your health probes stay green in the broken release. Isn't that cheating?

It is deliberate. A release that fails its probes is already handled by
Kubernetes: the pod never becomes ready. The interesting failure is the one
probes cannot see — the process is up, dependencies are reachable, but
requests fail or are slow. That is what metric analysis exists for, so the
demo reproduces exactly that case.

### 6. The steps say 10 % and 50 %. Is that really the traffic split?

No, and I say so in the docs. Without a traffic router, Argo Rollouts
approximates the weight with pod counts. With two replicas, "10 %" means one
canary pod next to two stable pods — about a third of the requests. Exact
weights need an ingress controller, Gateway API or a service mesh, which I
left out to keep the cluster small. The analysis is not affected because it
only reads the canary's own metrics.

### 7. What happens if the canary receives no traffic at all?

The queries end with `or on() vector(0)`, so "no data" becomes 0 and the check
passes. That is fail-open, chosen so a demo cluster does not error on empty
results, and the demo runs a load generator. For production I would fail
closed: add a minimum-request-rate condition or mark missing data as
inconclusive, so an unjudged canary cannot be promoted.

### 8. After the rollback, the Rollout shows `Degraded`. Why, and what do you do?

The abort protects traffic, but the desired state still asks for the bad
version. Argo Rollouts reports that mismatch as `Degraded`. The fix is to
revert the change that introduced it — `git revert` in a GitOps flow. The demo
script does the equivalent with `helm rollback`, after which the Rollout is
`Healthy` again without restarting any pod.

### 9. What does the anomaly detector add on top of fixed thresholds?

Thresholds are absolute; the detector is relative. A canary at 1.5 % errors or
300 ms p95 passes "< 2 %" and "< 500 ms" even if the stable version runs at
0 % and 20 ms. The detector scores how unlike the stable baseline the canary
is and publishes `release_anomaly_score`; the rollout fails at 0.7.
In a grossly broken release the error-rate check fires first — the detector
is slower by design (30 s cycle) and is meant for the subtle cases.

### 10. Explain the Isolation Forest in simple terms. Why did you choose it?

Each moment is a point: (error rate, p95 latency). The algorithm builds many
random trees that keep splitting the points until each is alone. An unusual
point is separated after very few splits; the average number of splits becomes
a score between 0 and 1. I chose it because it handles several metrics at once
without a threshold per metric, needs no labelled incidents, and the scoring
code stays about fifteen lines. I also know its limits here: two features, a
short baseline, no seasonality, and no validation against real incidents. A
simple canary-versus-stable ratio would be a legitimate alternative.

### 11. A healthy baseline has zero errors. How does a model learn from a flat line?

It cannot, which is why the scoring function adds small random noise to the
baseline: 0.5 percentage points for error rate and 20 ms for latency. That
noise defines "differences smaller than this do not matter". Without it, a
canary with 0.1 % errors would look infinitely far from a perfectly flat
baseline. The canary's values are also clamped to the baseline median so that
being *better* than the baseline is never an anomaly.

### 12. What is an SLO, and how do your alerts work?

The SLO is "99.5 % of HTTP requests succeed". The remaining 0.5 % is the error
budget. Instead of alerting on any error, the rules alert on the *burn rate* —
how fast the budget is being spent. A fast-burn alert (14.4× over one hour and
over five minutes) pages; a slow-burn alert (6× over six hours and thirty
minutes) opens a ticket. Using two windows means the alert is both significant
and still happening, and it clears quickly after recovery. This is the
multi-window, multi-burn-rate method from the Google SRE Workbook.

### 13. What is the difference between liveness and readiness, and how did you use them?

Liveness answers "should this container be restarted?"; readiness answers
"should this pod receive traffic?". `/healthz` checks only the process, so a
Redis outage does not cause restarts. `/readyz` checks dependencies for the
API and the worker. The web frontend's readiness deliberately does not check
the API, to avoid a cascading failure where one broken dependency removes
every frontend pod.

### 14. Why ArgoCD? And what is the difference between ArgoCD and Argo Rollouts?

ArgoCD keeps the cluster in sync with Git: it decides *what* runs. Argo
Rollouts controls *how* a change is rolled out. With pull-based GitOps, CI
needs no cluster credentials, drift is visible and corrected, and every change
has a commit. The repository uses the app-of-apps pattern: one root
Application creates the others. I should be clear that the local demo and the
CI test use `helm upgrade` directly for speed; the ArgoCD layout is provided
but not exercised by CI.

### 15. How does CI authenticate to Azure without storing a secret?

Through OIDC federation. The workflow requests a short-lived token from
GitHub that states which repository and branch it runs for. A managed identity
in Azure has a federated credential that trusts exactly that subject, and
Azure exchanges the token for an access token. There is no client secret to
store, leak or rotate. The IDs involved are stored as repository *variables*
because they are identifiers, not credentials.

### 16. How is the Terraform state handled, and why is there a bootstrap folder?

State is stored remotely in an Azure Storage container with versioning, and
accessed with Entra ID rather than account keys. The storage account and the
CI identity must exist before CI can run Terraform, so `infra/bootstrap`
creates them once from a laptop with local state. The platform configuration
then uses that backend. I have validated and linted the Terraform in CI but
have not applied it from this repository, to avoid cost.

### 17. How did you keep the Azure setup cheap, and what did that cost you?

Free-tier control plane, a single small burstable node, a daily cap on Log
Analytics ingestion, and a documented destroy path. The price is resilience:
no control-plane SLA, no redundancy, and a 4 GiB node that is tight for the
full observability stack. That is acceptable for a demo and would not be for
production.

### 18. How do you know the whole thing actually works?

Three levels. Unit tests for each service, including both fault modes and the
anomaly scoring. Helm lint and Terraform validate in CI. And an end-to-end
workflow that creates a kind cluster, deploys version 1, releases a broken
version and asserts that the rollout was aborted and only the old version is
serving. It uses the same scripts as the local demo, so the test cannot drift
from what a person runs.

### 19. What security practices are in the project?

Containers run as non-root with a read-only filesystem, no capabilities and
the default seccomp profile. Images are multi-stage and scanned with Trivy,
failing on fixable critical vulnerabilities. Image tags are immutable git
SHAs. Workflow permissions are read-only by default. Cloud authentication uses
OIDC. No secrets are committed; the Grafana password is generated into a
Kubernetes Secret. What is missing: network policies, TLS/ingress, image
signing and a secrets manager.

### 20. What would you do next, or differently, for production?

Add a traffic router for exact weights and header-based canaries. Make the
analysis fail closed on missing data. Replace the single Redis pod with a
managed queue with acknowledgements. Have CI open a pull request that bumps
the image tag, to close the GitOps loop. Add network policies, image signing
and digest pinning. Evaluate the anomaly detector against recorded incidents
before trusting it, or replace it with a simpler statistical comparison if it
does not earn its complexity.
