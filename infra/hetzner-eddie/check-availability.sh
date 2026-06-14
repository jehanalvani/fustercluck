#!/usr/bin/env bash
# Check which Hetzner server types can be created RIGHT NOW, per datacenter.
# Handy when `tofu apply` fails with `resource_unavailable` or
# "<type> is unavailable in <loc> and can no longer be ordered" — Hetzner's
# capacity (especially the cheap ARM CAX line) and per-location plan
# availability both fluctuate.
#
# Token resolution order: $HCLOUD_TOKEN, then $TF_VAR_hcloud_token, then the
# hcloud_token line in ./terraform.tfvars. The token is never printed.
#
# Usage:
#   cd infra/hetzner-eddie
#   ./check-availability.sh                      # default 4c/8g candidates
#   ./check-availability.sh cax21 cx33 cpx32     # specific types
set -euo pipefail

command -v jq   >/dev/null || { echo "jq is required (sudo apt install jq)"; exit 1; }
command -v curl >/dev/null || { echo "curl is required"; exit 1; }

TOKEN="${HCLOUD_TOKEN:-${TF_VAR_hcloud_token:-}}"
if [ -z "$TOKEN" ] && [ -f terraform.tfvars ]; then
  TOKEN=$(grep -E '^[[:space:]]*hcloud_token' terraform.tfvars | head -1 | cut -d'"' -f2 || true)
fi
[ -z "$TOKEN" ] && { echo "No token found (set HCLOUD_TOKEN or put hcloud_token in terraform.tfvars)"; exit 1; }

API="https://api.hetzner.cloud/v1"
ST=$(curl -s -H "Authorization: Bearer $TOKEN" "$API/server_types?per_page=100")
DC=$(curl -s -H "Authorization: Bearer $TOKEN" "$API/datacenters?per_page=100")

# Candidate types default to 4 vCPU / 8 GB across the ARM + x86 lines.
TYPES=("$@")
[ ${#TYPES[@]} -eq 0 ] && TYPES=(cax21 cax31 cx33 cpx31 cpx32)

for t in "${TYPES[@]}"; do
  ID=$(echo "$ST" | jq --arg n "$t" '.server_types[]|select(.name==$n).id')
  if [ -z "$ID" ] || [ "$ID" = "null" ]; then
    echo "$t: (no such server type)"
    continue
  fi
  specs=$(echo "$ST" | jq -r --arg n "$t" '.server_types[]|select(.name==$n)|"\(.cores)c/\(.memory)g \(.architecture)"')
  echo "$t  ($specs):"
  echo "$DC" | jq -r --argjson id "$ID" \
    '.datacenters[] | "  \(.name): " + (if ([.server_types.available[]|select(.==$id)]|length>0) then "AVAILABLE" else "—" end)'
done
