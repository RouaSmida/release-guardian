#!/usr/bin/env bash
# Create a local kind cluster with Argo Rollouts, Prometheus and version 1 of the app.
#   PROFILE=full (default)  kube-prometheus-stack with Grafana, plus Loki
#   PROFILE=lean            Prometheus Operator + Prometheus only (used by CI)
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require docker kind kubectl helm

step "1/6 kind cluster '$CLUSTER_NAME'"
if kind get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; then
  ok "cluster already exists"
else
  kind create cluster --name "$CLUSTER_NAME" --image "$KIND_NODE_IMAGE" --wait 120s
fi

step "2/6 Build images and load them into the cluster"
for image in "${IMAGES[@]}"; do
  info "building release-guardian/$image:$IMAGE_TAG"
  docker build --quiet -t "release-guardian/$image:$IMAGE_TAG" "$ROOT_DIR/apps/$image" >/dev/null
done
for image in "${IMAGES[@]}"; do
  kind load docker-image --name "$CLUSTER_NAME" "release-guardian/$image:$IMAGE_TAG" >/dev/null 2>&1 \
    || die "could not load release-guardian/$image:$IMAGE_TAG into the cluster"
done
ok "images loaded"

step "3/6 Argo Rollouts $ARGO_ROLLOUTS_VERSION"
k create namespace argo-rollouts --dry-run=client -o yaml | k apply -f - >/dev/null
k apply -n argo-rollouts -f \
  "https://github.com/argoproj/argo-rollouts/releases/download/$ARGO_ROLLOUTS_VERSION/install.yaml" >/dev/null
ok "applied"

step "4/6 Prometheus (kube-prometheus-stack $KPS_CHART_VERSION, profile: $PROFILE)"
h repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
h repo add grafana https://grafana.github.io/helm-charts >/dev/null 2>&1 || true
h repo update >/dev/null
values="$ROOT_DIR/deploy/observability/kube-prometheus-stack/values.yaml"
[ "$PROFILE" = "lean" ] && values="$ROOT_DIR/deploy/observability/kube-prometheus-stack/values-lean.yaml"
h upgrade --install kps prometheus-community/kube-prometheus-stack --version "$KPS_CHART_VERSION" \
  -n "$MONITORING_NAMESPACE" --create-namespace -f "$values" --wait --timeout 10m >/dev/null
# The operator creates the Prometheus pod after the Helm release is done.
for _ in $(seq 60); do
  k -n "$MONITORING_NAMESPACE" get pod -l app.kubernetes.io/name=prometheus -o name 2>/dev/null | grep -q . && break
  sleep 3
done
k -n "$MONITORING_NAMESPACE" wait pod -l app.kubernetes.io/name=prometheus --for=condition=Ready --timeout=300s >/dev/null
ok "Prometheus is ready"

if [ "$PROFILE" = "full" ]; then
  info "installing Loki $LOKI_CHART_VERSION and Alloy $ALLOY_CHART_VERSION (logs)"
  h upgrade --install loki grafana/loki --version "$LOKI_CHART_VERSION" -n "$MONITORING_NAMESPACE" \
      -f "$ROOT_DIR/deploy/observability/loki/values.yaml" --wait --timeout 5m >/dev/null \
    && h upgrade --install alloy grafana/alloy --version "$ALLOY_CHART_VERSION" -n "$MONITORING_NAMESPACE" \
      -f "$ROOT_DIR/deploy/observability/loki/alloy-values.yaml" --wait --timeout 5m >/dev/null \
    && ok "Loki is ready" \
    || warn "Loki/Alloy installation failed; continuing without logs (metrics and the demo are unaffected)"
fi

step "5/6 Deploy the application (version v1)"
k -n argo-rollouts rollout status deployment/argo-rollouts --timeout=180s >/dev/null
h upgrade --install "$RELEASE" "$CHART" -n "$NAMESPACE" --create-namespace \
  -f "$CHART/values-kind.yaml" \
  --set-string global.imageTag="$IMAGE_TAG" \
  --set-string services.web.version=v1 \
  --set-string services.api.version=v1 \
  --set-string services.worker.version=v1 \
  --wait --timeout 5m >/dev/null
for service in "${SERVICES[@]}"; do wait_rollout_healthy "$service" 300; done

step "6/6 Wait until Prometheus sees application traffic"
query="sum(rate(http_requests_total%7Bnamespace=%22$NAMESPACE%22%7D%5B1m%5D))"
rate=""
for _ in $(seq 40); do
  rate="$(prom_value "$query")"
  if [ -n "$rate" ] && [ "$rate" != "0" ]; then break; fi
  sleep 5
done
[ -n "$rate" ] && [ "$rate" != "0" ] || die "Prometheus has no http_requests_total data; check the PodMonitor (make diagnostics)"
ok "Prometheus measures $(printf '%.1f' "$rate") requests/s"

cat <<MSG

${BOLD}Release Guardian is running.${RESET}
  make demo-good   release a healthy version (promoted step by step)
  make demo-bad    release a broken version (rolled back automatically)
  make grafana     open the dashboards        (PROFILE=full only)
  make web         open the demo application
  make down        delete the cluster
MSG
