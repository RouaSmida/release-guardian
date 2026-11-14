#!/usr/bin/env bash
# Collect the state of the cluster into ./artifacts (uploaded by CI, handy locally).
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
set +e
OUT="${1:-$ROOT_DIR/artifacts}"
mkdir -p "$OUT"

k get nodes -o wide                                       > "$OUT/nodes.txt" 2>&1
k get pods -A -o wide                                     > "$OUT/pods.txt" 2>&1
k -n "$NAMESPACE" get rollouts,analysisruns,replicasets   > "$OUT/rollout-status.txt" 2>&1
k -n "$NAMESPACE" get rollouts -o yaml                    > "$OUT/rollouts.yaml" 2>&1
k -n "$NAMESPACE" get analysisruns -o yaml                > "$OUT/analysisruns.yaml" 2>&1
k -n "$NAMESPACE" get events --sort-by=.lastTimestamp     > "$OUT/events.txt" 2>&1
k -n "$NAMESPACE" describe pods                           > "$OUT/pods-describe.txt" 2>&1
k -n "$NAMESPACE" logs -l app.kubernetes.io/part-of=release-guardian \
  --all-containers --prefix --tail=300                    > "$OUT/app-logs.txt" 2>&1
k -n argo-rollouts logs deployment/argo-rollouts --tail=500 > "$OUT/argo-rollouts-controller.log" 2>&1
h list -A                                                 > "$OUT/helm-releases.txt" 2>&1
h history "$RELEASE" -n "$NAMESPACE"                      > "$OUT/helm-history.txt" 2>&1
if kubectl argo rollouts version >/dev/null 2>&1; then
  for service in "${SERVICES[@]}"; do
    kubectl argo rollouts --context "kind-$CLUSTER_NAME" -n "$NAMESPACE" get rollout "$service" --no-color
  done > "$OUT/rollouts-tree.txt" 2>&1
fi

# What Prometheus knows: pods per service/version/role, and the anomaly scores.
prom="/api/v1/namespaces/$MONITORING_NAMESPACE/services/prometheus-operated:9090/proxy/api/v1"
k get --raw "$prom/query?query=count%20by%20(service,version,rollout_role,rollouts_pod_template_hash)%20(app_info)" \
  > "$OUT/prometheus-app-info.json" 2>&1
k get --raw "$prom/query?query=release_anomaly_score%5B10m%5D" > "$OUT/prometheus-anomaly-score.json" 2>&1
k get --raw "$prom/targets?state=active" > "$OUT/prometheus-targets.json" 2>&1

echo "diagnostics written to $OUT"
