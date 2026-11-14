#!/usr/bin/env bash
# Release a new version of one service as a canary and verify the outcome.
#   scripts/release.sh good   healthy version  -> must be promoted
#   scripts/release.sh bad    broken version   -> must be aborted and rolled back
# Environment: SERVICE (default: api), FAULT_MODE for "bad" (errors | latency).
# With FAULT_MODE=latency the script also asserts that the anomaly detector
# scored the canary above 0 while it was running (ASSERT_ANOMALY=0 to skip).
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require kubectl helm

MODE="${1:-}"
SERVICE="${SERVICE:-api}"
TIMEOUT="${TIMEOUT:-420}"
case "$MODE" in
  good) FAULT="none" ;;
  bad)  FAULT="${FAULT_MODE:-errors}" ;;
  *)    die "usage: $0 good|bad" ;;
esac

current_version() {
  rollout_field "$SERVICE" '{.spec.template.spec.containers[0].env[?(@.name=="APP_VERSION")].value}'
}
pods_with_hash() {
  k -n "$NAMESPACE" get pods -l "app.kubernetes.io/name=$SERVICE,rollouts-pod-template-hash=$1" \
    --field-selector=status.phase=Running -o name 2>/dev/null | wc -l | tr -d ' '
}
analysis_field() {
  k -n "$NAMESPACE" get analysisrun -l "rollouts-pod-template-hash=$1" \
    --sort-by=.metadata.creationTimestamp -o "jsonpath={.items[-1:].status.$2}" 2>/dev/null || true
}
served_versions() {
  k -n "$NAMESPACE" get pods -l "app.kubernetes.io/name=$SERVICE" \
    -o 'jsonpath={range .items[?(@.status.phase=="Running")]}{.spec.containers[0].env[?(@.name=="APP_VERSION")].value}{"\n"}{end}' \
    | sort -u | paste -sd, -
}

[ "$(rollout_field "$SERVICE" '{.status.phase}')" = "Healthy" ] \
  || die "rollout/$SERVICE is not Healthy; wait or run 'make status' before releasing"

ASSERT_ANOMALY="${ASSERT_ANOMALY:-$([ "$FAULT" = "latency" ] && echo 1 || echo 0)}"
selector="%7Bnamespace=%22$NAMESPACE%22,service=%22$SERVICE%22%7D"
anomaly_query="max(release_anomaly_score$selector)"
positive() { awk -v v="${1:-0}" 'BEGIN { exit !(v + 0 > 0) }'; }

# A previous canary stays in the 1-minute rate window for a while after it is
# gone. Start from a score of 0 so this release is judged on its own.
for _ in $(seq 30); do
  positive "$(prom_value "$anomaly_query")" || break
  info "waiting for the anomaly score of the previous canary to return to 0"
  sleep 5
done

OLD_VERSION="$(current_version)"
OLD_HASH="$(rollout_field "$SERVICE" '{.status.stableRS}')"
number="${OLD_VERSION//[^0-9]/}"
NEW_VERSION="v$(( ${number:-1} + 1 ))"
[ "$MODE" = "bad" ] && NEW_VERSION="$NEW_VERSION-broken"

step "Releasing $SERVICE $OLD_VERSION -> $NEW_VERSION (FAULT_MODE=$FAULT)"
info "canary steps: 10% -> pause -> 50% -> pause -> 100%"
info "analysis    : error rate < 2%, p95 latency < 500 ms, anomaly score < 0.7"
h upgrade "$RELEASE" "$CHART" -n "$NAMESPACE" --reuse-values \
  --set-string "services.$SERVICE.version=$NEW_VERSION" \
  --set-string "services.$SERVICE.faultMode=$FAULT" >/dev/null

step "Watching the rollout"
start=$SECONDS
outcome="timeout"
NEW_HASH=""
while (( SECONDS - start < TIMEOUT )); do
  phase="$(rollout_field "$SERVICE" '{.status.phase}')"
  aborted="$(rollout_field "$SERVICE" '{.status.abort}')"
  stable="$(rollout_field "$SERVICE" '{.status.stableRS}')"
  hash="$(rollout_field "$SERVICE" '{.status.currentPodHash}')"
  step_index="$(rollout_field "$SERVICE" '{.status.currentStepIndex}')"
  if [ -n "$hash" ] && [ "$hash" != "$OLD_HASH" ]; then
    NEW_HASH="$hash"
    printf '    [%3ss] phase=%-11s step=%s/4  canary pods=%s  stable pods=%s  analysis=%-10s anomaly score=%s\n' \
      "$(( SECONDS - start ))" "$phase" "${step_index:-0}" \
      "$(pods_with_hash "$NEW_HASH")" "$(pods_with_hash "$OLD_HASH")" \
      "$(analysis_field "$NEW_HASH" phase || true)" "$(prom_value "$anomaly_query" | cut -c1-4)"
    if [ "$aborted" = "true" ]; then outcome="aborted"; break; fi
    if [ "$phase" = "Healthy" ] && [ "$stable" = "$NEW_HASH" ]; then outcome="promoted"; break; fi
  fi
  sleep 5
done

if [ "$outcome" = "timeout" ]; then
  die "rollout did not finish within ${TIMEOUT}s (run 'make diagnostics')"
fi

if [ "$MODE" = "good" ]; then
  [ "$outcome" = "promoted" ] || die "expected the healthy release to be promoted, but it was $outcome: $(analysis_field "$NEW_HASH" message)"
  ok "$NEW_VERSION passed every analysis and was promoted to 100%"
  info "versions now serving: $(served_versions)"
  exit 0
fi

# ---- bad release: it must have been aborted, and the old version must still serve ----
[ "$outcome" = "aborted" ] || die "expected the broken release to be aborted, but it was $outcome"
elapsed=$(( SECONDS - start ))
ok "Argo Rollouts ABORTED the rollout after ${elapsed}s"
info "reason: $(analysis_field "$NEW_HASH" message)"

# Highest anomaly score Prometheus recorded between the release and the abort.
peak="$(prom_value "max_over_time(release_anomaly_score$selector%5B${elapsed}s%5D)")"
if positive "$peak"; then
  ok "anomaly detector scored the canary: peak release_anomaly_score = $(printf '%.2f' "$peak")"
elif [ "$ASSERT_ANOMALY" = "1" ]; then
  info "last anomaly-detector log lines:"
  k -n "$NAMESPACE" logs deployment/anomaly-detector --tail=6 2>&1 | sed 's/^/      /' || true
  die "release_anomaly_score stayed at 0 while the canary was running (expected > 0)"
else
  warn "release_anomaly_score stayed at 0 while the canary was running (not asserted for FAULT_MODE=$FAULT)"
fi

step "Verifying the automatic rollback"
for _ in $(seq 30); do
  [ "$(served_versions)" = "$OLD_VERSION" ] && [ "$(pods_with_hash "$NEW_HASH")" = "0" ] && break
  sleep 3
done
[ "$(served_versions)" = "$OLD_VERSION" ] || die "pods still serve: $(served_versions) (expected only $OLD_VERSION)"
[ "$(rollout_field "$SERVICE" '{.status.stableRS}')" = "$OLD_HASH" ] || die "stable ReplicaSet changed unexpectedly"
ok "canary pods are gone; all traffic is served by $OLD_VERSION again"

step "Restoring the desired state"
info "Traffic is already safe. The Rollout still *wants* $NEW_VERSION, so it reports Degraded"
info "until the bad change is reverted (with GitOps: 'git revert'; here: 'helm rollback')."
h rollback "$RELEASE" -n "$NAMESPACE" >/dev/null
wait_rollout_healthy "$SERVICE" 180

printf '\n%s%s  ROLLBACK VERIFIED: the broken release never reached more than a share of traffic.%s\n' "$BOLD" "$GREEN" "$RESET"
