# Eddie — cloud automation brain (Hetzner / Helsinki)

Eddie is an always-on automation/agent stack running on its own single-node k3s
cluster at Hetzner (Helsinki, Finland — EU). It is deliberately separate from the
home cluster so it stays up independent of home power/internet.

**Why Helsinki (EU) over Hillsboro (US-West):** EU/German jurisdiction. Data on an
EU Hetzner node falls under GDPR and is shielded from the US CLOUD Act (Hetzner
Online GmbH has no US parent). The cost is ~150 ms latency to a PNW home — which is
imperceptible for trigger-based automation. To flip back to US-West, set
`location = "hil"` in `terraform.tfvars` before applying; nothing else cares.

**If a latency-sensitive workload shows up later** (chatty, multi-round-trip, or
near-real-time interaction with home devices), we address it *then* rather than
compromising the whole cluster's jurisdiction now. Options, in order of preference:
1. Run just that piece on the **home cluster** (kube02/03) — it's the same Helm
   release; place it where the latency demands. Eddie stays the always-on cloud spine.
2. Stand up a **second node in `hil`** (Hillsboro) for that workload only.
3. Push the latency-bound logic to an **edge/webhook relay** at home that Eddie calls.
Most automation is trigger-based and won't need any of this.

**Stack:** n8n (orchestration) + LiteLLM (model router) + Qdrant (vector memory)
+ SearXNG (private search). No GPU — inference goes to Claude and serverless
open models via LiteLLM. See "Bringing Eddie home" for the on-prem path.

> **Status (2026-06-14): LIVE.** `cx33` (Intel 4 vCPU/8 GB) in `hel1` (Helsinki),
> IP `65.108.152.20`. All four pods Running, Let's Encrypt cert issued, n8n
> reachable at https://n8n.alvani.me. Cost ~$7–8/mo (cx33 + volume) ≈ ~$100/year.
>
> Provisioning notes for next time:
> - ARM (cax21, ~half price) was **sold out across all Hetzner locations**, and
>   `fsn1` had no 4c/8g x86 free either — hence `cx33`/`hel1`. Run
>   `infra/hetzner-eddie/check-availability.sh` to re-check; swap to `cax21` when
>   ARM returns (the data volume persists through a server replace).
> - litellm needs ~1.5 Gi memory (OOMKilled at 512 Mi).
> - The control node (20-size) needs: ansible + `kubernetes.core` +
>   `python3-kubernetes` + helm. Use `ANSIBLE_VAULT_IDENTITY_LIST=fustercluck@<file>`
>   to bypass the `op`-based vault password when running from a host without 1Password.
> - Diagnostics: `infra/hetzner-eddie/eddie-diag.sh` (secret-free) piped to a paste,
>   or `--logs` for verbose (treat as sensitive).

## Architecture

```
                         Internet
                            │  :443 (Traefik + Let's Encrypt)
                            ▼
   n8n.alvani.me ──► [ k3s @ Hetzner Helsinki, CX33 (x86) ]
                            │
        ┌───────────────────┼────────────────────────┐
        ▼                   ▼                         ▼
      n8n  ──────────►  LiteLLM  ──────►  Anthropic (claude)
   (public UI)        (ClusterIP)        Groq / Together (open models)
        │                                 [future: Mac Mini Ollama]
        ├──────────►  Qdrant  (ClusterIP, vector memory)
        └──────────►  SearXNG (ClusterIP, private search)
```

Only n8n is exposed publicly. LiteLLM/Qdrant/SearXNG are ClusterIP-only.

## Layout

| Path | Purpose |
|------|---------|
| `infra/hetzner-eddie/` | OpenTofu — provisions the Hetzner node (server, firewall, volume, SSH key) |
| `inventory_eddie.yml` | Ansible inventory for the cloud node (fill in the IP) |
| `deploy_eddie.yml` | Installs k3s + cert-manager + the Eddie apps |
| `create_eddie_secrets.yml` | Creates k8s Secrets from `vars/eddie_vault.yml` |
| `vars/eddie.yml` | Non-secret config (hostnames, versions, namespace) |
| `vars/eddie_vault.yml.example` | Template for the vault-encrypted secrets |
| `k8s/apps/ai/eddie/*.yml` | bjw-s app-template values: n8n, litellm, qdrant, searxng |

## Why hybrid (OpenTofu + Ansible)

OpenTofu owns the **infra lifecycle** — its state file gives a clean `tofu destroy`
so teardown never orphans a billable volume/IP, plus drift detection. Ansible owns
**everything above the OS** (k3s + Helm + apps), which is where your existing
expertise lives. The OpenTofu surface is intentionally tiny (~5 resources).

## First-time setup

### 0. Prereqs (controller / your machine)
```bash
# OpenTofu (or terraform), helm, kubectl
ansible-galaxy collection install kubernetes.core
# Hetzner API token: Cloud Console → project → Security → API Tokens (Read & Write)
```

### 1. Provision the node (OpenTofu)
```bash
cd infra/hetzner-eddie
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars: hcloud_token + admin_cidrs (your home IP: curl ifconfig.me)
tofu init
tofu apply
tofu output eddie_ipv4        # note this IP
cp terraform.tfstate /whidbey/backups/eddie-tfstate-$(date +%F).backup   # back up state!
```

### 2. DNS
Create a **public** A record: `n8n.alvani.me → <eddie_ipv4>`.

> **Split-horizon gotcha:** Pi-hole resolves `*.alvani.me → 10.0.1.30` (home Traefik)
> on the LAN. So on your home network `n8n.alvani.me` would point at the *home*
> cluster, not Hetzner. Options: (a) use a subdomain carved out of the split-horizon
> rule, or (b) add a Pi-hole local DNS override for `n8n.alvani.me → <eddie_ipv4>`.
> Public/mobile resolution is unaffected.

### 3. Point inventory at the node
Edit `inventory_eddie.yml` → set `ansible_host` to the `eddie_ipv4` output.

### 4. Install k3s + apps
```bash
ansible-playbook -i inventory_eddie.yml deploy_eddie.yml
```

### 5. Create secrets
```bash
cp vars/eddie_vault.yml.example vars/eddie_vault.yml
# Generate values:
#   openssl rand -hex 32   # n8n encryption key + searxng secret
#   openssl rand -hex 24   # litellm master key + qdrant api key
# Add your ANTHROPIC_API_KEY (and optional TOGETHER/GROQ keys).
ansible-vault encrypt vars/eddie_vault.yml --vault-id fustercluck@passfile.txt
ansible-playbook -i inventory_eddie.yml create_eddie_secrets.yml --vault-id fustercluck@passfile.txt
# restart n8n so it picks up the encryption key if it started first:
KUBECONFIG=.kube/eddie.yaml kubectl -n ai rollout restart deploy
```

### 6. Open Eddie
Visit `https://n8n.alvani.me`, create the owner account, and wire workflows.
Point n8n's AI/HTTP nodes at the in-cluster brain endpoints (already injected as
`LITELLM_BASE_URL`, `QDRANT_URL`, `SEARXNG_URL` env vars).

## 🔑 The one thing you must not lose
`vault_n8n_encryption_key` encrypts **every credential n8n stores**. Generate it
**once**, keep it in the vault, and back it up. Rotate/lose it → all stored n8n
credentials are unrecoverable. Also back up the node's data volume (Qdrant + n8n
SQLite live on `/mnt/eddie-data`).

## Day-2

```bash
# kubectl/helm against Eddie
export KUBECONFIG=$(pwd)/.kube/eddie.yaml
kubectl get pods -n ai

# Update an app: edit k8s/apps/ai/eddie/<app>.yml, then re-run
ansible-playbook -i inventory_eddie.yml deploy_eddie.yml

# Tear it ALL down (stops billing cleanly)
cd infra/hetzner-eddie && tofu destroy
```

## Adding a second Eddie
Copy `k8s/apps/ai/eddie/n8n.yml` → `n8n-work.yml`, change the hostname + PVC,
add the release name to the loop in `deploy_eddie.yml`. LiteLLM/Qdrant/SearXNG are
shared — give each Eddie its own Qdrant collection for memory isolation.

## Bringing Eddie home (future)
The whole point of going k8s-native: migration is cheap. When you want Eddie local —
e.g. on a **Mac Mini** (Apple Silicon is excellent for local LLMs) — you have two
moves, independent of each other:

1. **Inference home first (keep Eddie at Hetzner):** run Ollama on the Mac Mini,
   uncomment the `local` route in `k8s/apps/ai/eddie/litellm.yml`, point `api_base`
   at the Mac Mini. Eddie keeps asking LiteLLM for model `local`; only the target
   changes. Zero workflow edits.
2. **Eddie home fully:** the apps are plain Helm releases — deploy the same
   `k8s/apps/ai/eddie/*.yml` onto the home cluster (or the Mac Mini's k3s),
   move DNS, and `tofu destroy` the Hetzner node.

Local inference belongs on the Mac Mini, **not** the 3-Pi cluster (CPU-only ARM,
already loaded — realistically only 1–3B models / embeddings) and **not** back on
20-size as-is (NFS lock contention starved Ollama; needs resource isolation first).
```
