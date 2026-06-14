#!/usr/bin/env bash
# Eddie diagnostics → STDOUT. Secret-free by DEFAULT, so it's safe to paste
# publicly (the repo is public). It includes pod status, services, ingress,
# certificates, events, and `describe` — which shows secret *names* via
# secretKeyRef but NEVER their values, and the only plaintext env vars in this
# stack are non-secret service URLs.
#
# Container LOGS are EXCLUDED by default because they can leak secrets. Pass
# --logs to include them, and then share that output only through a private
# channel (not a public paste/repo).
#
#   ./eddie-diag.sh | curl -F'file=@-' https://0x0.st     # safe public paste → URL
#   ./eddie-diag.sh --logs > /tmp/diag.txt                # includes logs; keep PRIVATE
set -uo pipefail
export KUBECONFIG="${KUBECONFIG:-$HOME/fustercluck/.kube/eddie.yaml}"
NS=ai
WANT_LOGS=0
[ "${1:-}" = "--logs" ] && WANT_LOGS=1

echo "### $(date -u) — Eddie diagnostics$([ "$WANT_LOGS" = 1 ] && echo ' (+LOGS — treat as SENSITIVE)' || echo ' (secret-free)')"
echo
echo "### pods"
kubectl get pods -n "$NS" -o wide
echo
echo "### svc / ingress / certificate"
kubectl get svc,ingress,certificate -n "$NS"
echo
echo "### events (last 30)"
kubectl get events -n "$NS" --sort-by=.lastTimestamp 2>/dev/null | tail -30

for pod in $(kubectl get pods -n "$NS" -o name 2>/dev/null); do
  echo
  echo "==================== $pod ===================="
  kubectl describe -n "$NS" "$pod" 2>&1 | sed -n '/^Containers:/,$p' | tail -60
  if [ "$WANT_LOGS" = 1 ]; then
    echo "--- logs (current) ---"
    kubectl logs -n "$NS" "$pod" --tail=50 2>&1
    echo "--- logs (previous crash, if any) ---"
    kubectl logs -n "$NS" "$pod" --previous --tail=50 2>&1
  fi
done
