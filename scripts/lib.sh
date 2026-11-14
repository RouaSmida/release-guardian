#!/usr/bin/env bash
# Shared settings and helpers. Sourced by the other scripts.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/versions.env
source "$ROOT_DIR/scripts/versions.env"

CLUSTER_NAME="${CLUSTER_NAME:-release-guardian}"
NAMESPACE="${NAMESPACE:-release-guardian}"
MONITORING_NAMESPACE="monitoring"
RELEASE="release-guardian"
CHART="$ROOT_DIR/deploy/charts/release-guardian"
IMAGE_TAG="${IMAGE_TAG:-dev}"
PROFILE="${PROFILE:-full}"   # full = with Grafana and Loki, lean = Prometheus only (CI)
SERVICES=(web api worker)
IMAGES=(web api worker anomaly-detector)

if [ -t 1 ]; then
  BOLD=$'\033[1m'; RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; BLUE=$'\033[34m'; RESET=$'\033[0m'
else
  BOLD=""; RED=""; GREEN=""; YELLOW=""; BLUE=""; RESET=""
fi

step() { printf '\n%s==> %s%s\n' "$BOLD$BLUE" "$*" "$RESET"; }
info() { printf '    %s\n' "$*"; }
ok()   { printf '%s  ✔ %s%s\n' "$GREEN" "$*" "$RESET"; }
warn() { printf '%s  ! %s%s\n' "$YELLOW" "$*" "$RESET"; }
die()  { printf '%s  ✘ %s%s\n' "$RED" "$*" "$RESET" >&2; exit 1; }

require() {
  for tool in "$@"; do
    command -v "$tool" >/dev/null 2>&1 || die "'$tool' is required but not installed"
  done
}

k() { kubectl --context "kind-$CLUSTER_NAME" "$@"; }
h() { helm --kube-context "kind-$CLUSTER_NAME" "$@"; }

# rollout_field <service> <jsonpath>
rollout_field() { k -n "$NAMESPACE" get rollout "$1" -o "jsonpath=$2" 2>/dev/null || true; }

# Value of an instant PromQL query (already URL-encoded), through the API server proxy.
prom_value() {
  k get --raw "/api/v1/namespaces/$MONITORING_NAMESPACE/services/prometheus-operated:9090/proxy/api/v1/query?query=$1" 2>/dev/null \
    | sed -n 's/.*"value":\[[^,]*,"\([^"]*\)"\].*/\1/p'
}

# wait_rollout_healthy <service> [timeout-seconds]
wait_rollout_healthy() {
  local service="$1" timeout="${2:-300}" start=$SECONDS phase
  while (( SECONDS - start < timeout )); do
    phase="$(rollout_field "$service" '{.status.phase}')"
    if [ "$phase" = "Healthy" ]; then ok "rollout/$service is Healthy"; return 0; fi
    sleep 3
  done
  die "rollout/$service not Healthy after ${timeout}s (phase: ${phase:-unknown})"
}
