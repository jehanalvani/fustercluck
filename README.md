# fustercluck

Ansible + k3s home lab cluster. Provisions nodes, bootstraps a k3s Kubernetes cluster, and deploys a media/monitoring/home automation stack.

## Cluster Topology

| Host | Role | Labels/Taints |
|---|---|---|
| `kube01.local` (10.0.1.30) | k3s control plane | — |
| `kube02.local` (10.0.1.50) | worker | `homekit-bridge=true` |
| `kube03.local` (10.0.1.49) | worker | `monitoring=true` |
| `20-size.local` (10.0.1.203) | worker, NFS server, GPU node | `dedicated=media:NoSchedule` |
| `artpi.local` / `displaypi.local` | toys | — |

## Applications

| App | Namespace | URL |
|---|---|---|
| nzbget, transmission | `media` | `nzbget.alvani.me`, `transmission.alvani.me` |
| radarr, sonarr, lidarr, readarr, prowlarr, ombi | `media` | `<app>.alvani.me` |
| plex | `plex` | `plex.alvani.me` |
| prometheus, grafana, influxdb | `monitoring` | `<app>.alvani.me` |
| homebridge | `homebridge` | `homebridge.alvani.me` |
| shinobi | `security` | `shinobi.alvani.me` |
| traefik dashboard | `kube-system` | `traefik.alvani.me` |

All services use TLS via **Let's Encrypt DNS-01** (Cloudflare). Same URL works on the home network (via Pi-hole split-DNS) and externally (via Cloudflare → Orbi → kube01).

## Networking

### DNS (split-DNS via Pi-hole)
Pi-hole runs on `20-size.local` at `10.0.1.250` on a macvlan Docker network.
- `*.alvani.me` → `10.0.1.30` (kube01, Traefik/klipper-lb) when on the home network
- Everything else → Unlocator (185.37.37.37) + Cloudflare (1.1.1.1)
- Web UI: `http://10.0.1.250/admin`

Orbi is configured to use `10.0.1.250` as its upstream DNS.

> **Note:** Do not point `*.alvani.me` at 20-size (10.0.1.203) — klipper-lb does not run there due to the `dedicated=media:NoSchedule` taint. See `roles/pihole/PIHOLE.md`.

### Ingress
Traefik (k3s built-in) handles all ingress. LoadBalancer IP includes kube01/02/03.
Orbi forwards ports 80/443 → `10.0.1.30` (kube01).
Cloudflare proxies `*.alvani.me` → home public IP.

### TLS
cert-manager with `letsencrypt-issuer` ClusterIssuer using Cloudflare DNS-01.
Cloudflare API token stored in `cert-manager/cloudflare-api-token` secret.
Add `vault_cloudflare_api_token` to `roles/common/vars/vault.yml` for reproducibility.

## Full Deploy Order

### 1. Provision the cluster

```bash
ansible-playbook k3s_init.yml
```

Installs k3s on all nodes: common baseline → server → agents. Safe to re-run.

### 2. Bootstrap cluster infrastructure

```bash
ansible-playbook cluster_bootstrap.yml
```

Installs: Sealed Secrets, cert-manager + ClusterIssuers, NFS storage provisioner, Nvidia device plugin, Traefik config + dashboard ingress.

### 3. Create application secrets

```bash
ansible-playbook create_secrets.yml
```

Reads from Ansible Vault and creates Kubernetes secrets for nzbget, grafana, influxdb, shinobi, and the Cloudflare API token for cert-manager. See [Secrets](#secrets) below.

### 4. Deploy applications

```bash
ansible-playbook deploy_apps.yml
```

Deploys all apps via `helm upgrade --install` using `bjw-s/app-template`. Safe to re-run.

```bash
# Deploy a single app or group
ansible-playbook deploy_apps.yml --tags sonarr
ansible-playbook deploy_apps.yml --tags media
ansible-playbook deploy_apps.yml --tags plex,monitoring
```

### 5. Configure 20-size (NFS/Pi-hole node)

```bash
ansible-playbook 20-size_config.yml
```

Sets up NFS exports, NFS directory structure, Docker, and Pi-hole on 20-size. Plex and Shinobi are deployed via Helm (step 4) — they run on 20-size as k8s workloads, not Docker containers.

## Secrets

All secrets are sourced from Ansible Vault. Secret values live in:
- `roles/common/vars/vault.yml` — user passwords, app passwords, `vault_cloudflare_api_token`

Vault password file: `passfile.txt` (not committed).

### Plex claim token

`plex-secret` is not created by `create_secrets.yml` because `PLEX_CLAIM` is a one-time 4-minute token. Before deploying Plex for the first time:

1. Go to **plex.tv/claim** while logged into your Plex account
2. Run immediately:

```bash
kubectl create secret generic plex-secret \
  --from-literal=PLEX_CLAIM='claim-xxxxxxxxxxxxxxxxx' \
  -n plex

ansible-playbook deploy_apps.yml --tags plex
```

Once Plex starts and registers, the token is consumed. Remove `PLEX_CLAIM` from `k8s/apps/plex/values.yml` after initial setup.

## Adding a New Node

### Raspberry Pi

1. Flash SD card, touch `ssh` in the boot volume (`/Volumes/bootfs` on macOS)
2. SSH to the host using default credentials
3. Copy `ansible` user SSH key to the new host
4. Add host to `inventory.yml`
5. Run `k3s_init.yml` (safe to re-run against existing nodes)

### Generic host

1. Create `ansible` user with sudo access and copy SSH key
2. Add to `inventory.yml` in the appropriate group
3. Run relevant playbook

## Other Playbooks

| Playbook | Purpose |
|---|---|
| `reboot.yml` | Reboot nodes |
| `configure_arr_apps.yml` | Configure arr app settings via API (unmonitor deleted, Plex metadata) |
| `configure_cameras.yml` | Configure Amcrest camera hardware (motion, orientation, users) |
| `configure_transmission.yml` | Create Transmission directory structure on NFS before first deploy |

## Requirements

- Ansible with `kubernetes.core` and `community.docker` collections:
  ```bash
  ansible-galaxy collection install kubernetes.core community.docker ansible.posix
  ```
- `helm` on the control machine
- `kubectl` configured to reach the cluster (kubeconfig distributed by `k3s_init.yml`)
