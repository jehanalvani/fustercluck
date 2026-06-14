#!/usr/bin/env bash
# Eddie pre-deploy preflight — read-only checks, makes no changes.
# Run from the control node (e.g. 20-size) inside infra/hetzner-eddie/.
#
#   ./preflight.sh                 # uses `tofu output eddie_ipv4` for the IP
#   ./preflight.sh 65.108.152.20   # or pass the IP explicitly
#
# Verifies three things before you run deploy_eddie.yml:
#   1. SERVER   — SSH in as ansible, data volume mounted, OS is Ubuntu
#   2. DNS      — eddie.alvani.me resolves, and where (warns if it points home)
#   3. TOOLCHAIN— ansible + kubernetes.core + python kubernetes + helm present

HOST_FQDN="${EDDIE_HOST:-eddie.alvani.me}"
SSH_KEY="${EDDIE_SSH_KEY:-$HOME/.ssh/eddie}"
SSH_USER="${EDDIE_SSH_USER:-ansible}"

IP="${1:-}"
[ -z "$IP" ] && IP="$(tofu output -raw eddie_ipv4 2>/dev/null || true)"

pass() { printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$1"; }

echo "== 1. SERVER ($SSH_USER@${IP:-<unknown>}) =="
if [ -z "$IP" ]; then
  fail "No IP (pass one as arg, or run from the dir with tofu state)"
elif [ ! -f "$SSH_KEY" ]; then
  fail "SSH key not found at $SSH_KEY (set EDDIE_SSH_KEY=...)"
else
  out=$(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 \
        "$SSH_USER@$IP" 'whoami; df -h --output=target,size /mnt/eddie-data 2>/dev/null | tail -1; . /etc/os-release; echo "$PRETTY_NAME"' 2>&1)
  if [ $? -eq 0 ]; then
    who=$(echo "$out" | sed -n 1p); vol=$(echo "$out" | sed -n 2p); os=$(echo "$out" | sed -n 3p)
    [ "$who" = "$SSH_USER" ] && pass "SSH login as $who" || warn "logged in as '$who' (expected $SSH_USER)"
    echo "$vol" | grep -q /mnt/eddie-data && pass "data volume mounted: $vol" || fail "no mount at /mnt/eddie-data"
    echo "$os" | grep -qi ubuntu && pass "OS: $os" || warn "OS: $os"
  else
    fail "SSH failed: $out"
  fi
fi

echo "== 2. DNS ($HOST_FQDN) =="
resolved=$(getent hosts "$HOST_FQDN" | awk '{print $1}' | head -1)
if [ -z "$resolved" ]; then
  fail "$HOST_FQDN does not resolve (add the public A record + Pi-hole entry)"
elif [ -n "$IP" ] && [ "$resolved" = "$IP" ]; then
  pass "$HOST_FQDN → $resolved (matches server)"
elif [ "$resolved" = "10.0.1.30" ]; then
  warn "$HOST_FQDN → $resolved (home Traefik!) — add a Pi-hole override → $IP"
else
  warn "$HOST_FQDN → $resolved (expected $IP) — check A record / Pi-hole / propagation"
fi

echo "== 3. CONTROL-NODE TOOLCHAIN =="
command -v ansible-playbook >/dev/null && pass "ansible ($(ansible --version 2>/dev/null | head -1))" \
  || fail "ansible missing  → sudo apt install -y ansible  (or pipx install ansible)"
if command -v ansible-galaxy >/dev/null && ansible-galaxy collection list 2>/dev/null | grep -q 'kubernetes.core'; then
  pass "ansible collection kubernetes.core"
else
  fail "kubernetes.core missing  → ansible-galaxy collection install kubernetes.core"
fi
python3 -c 'import kubernetes' 2>/dev/null && pass "python kubernetes lib" \
  || fail "python kubernetes missing  → pip install --user kubernetes  (needed by kubernetes.core modules)"
command -v helm >/dev/null && pass "helm ($(helm version --short 2>/dev/null))" \
  || fail "helm missing  → curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash"
command -v kubectl >/dev/null && pass "kubectl present" || warn "kubectl missing (optional; handy for poking the cluster)"

echo
echo "Fix any ✗ above, then: ansible-playbook -i inventory_eddie.yml deploy_eddie.yml"
