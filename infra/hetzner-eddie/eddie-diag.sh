#!/usr/bin/env bash
# Collect Eddie cluster diagnostics into a file and push it — so cluster state
# can be reviewed without copy-pasting from a mobile terminal.
#
#   ./eddie-diag.sh            # gathers everything, commits, pushes
#
# The output is committed to infra/hetzner-eddie/diag-output.txt. It does a light
# redaction pass on key-like strings, but treat it as sensitive: DELETE it after
# review (a follow-up commit will remove it).
set -uo pipefail
export KUBECONFIG="${KUBECONFIG:-$HOME/fustercluck/.kube/eddie.yaml}"
NS=ai
OUT="$HOME/fustercluck/infra/hetzner-eddie/diag-output.txt"

# crude redaction: mask sk-... keys and long hex blobs so they don't land in git
redact() { sed -E 's/(sk-[A-Za-z0-9_-]{4})[A-Za-z0-9_-]+/\1__REDACTED/g; s/\b([A-Fa-f0-9]{8})[A-Fa-f0-9]{24,}\b/\1__REDACTED/g'; }

{
  echo "### $(date -u) — Eddie diagnostics"
  echo
  echo "### kubectl get pods -o wide"
  kubectl get pods -n "$NS" -o wide
  echo
  echo "### kubectl get svc,ingress,certificate"
  kubectl get svc,ingress,certificate -n "$NS"
  echo
  echo "### recent events"
  kubectl get events -n "$NS" --sort-by=.lastTimestamp 2>/dev/null | tail -30
  echo
  for pod in $(kubectl get pods -n "$NS" -o name 2>/dev/null); do
    echo "==================== $pod ===================="
    echo "--- describe (status + events) ---"
    kubectl describe -n "$NS" "$pod" 2>&1 | sed -n '/^Containers:/,$p' | tail -55
    echo "--- logs (current) ---"
    kubectl logs -n "$NS" "$pod" --tail=40 2>&1
    echo "--- logs (previous crash, if any) ---"
    kubectl logs -n "$NS" "$pod" --previous --tail=40 2>&1
    echo
  done
} 2>&1 | redact > "$OUT"

cd "$HOME/fustercluck" || exit 1
git add -f "$OUT"
git commit -q -m "temp: eddie diag snapshot" && {
  for i in 1 2 3 4; do git push && break || sleep $((2**i)); done
}
echo "Done — pushed $OUT"
