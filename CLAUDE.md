# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is a home lab infrastructure-as-code project using Ansible to provision and manage a 4-node k3s Kubernetes cluster plus supporting services (DNS, NFS, Docker). The cluster runs a media/monitoring/home-automation stack.

## Common Commands

All playbooks require the vault password file:

```bash
# Full cluster init (common → k3s server → agents → node labels/taints)
ansible-playbook k3s_init.yml --vault-id fustercluck@passfile.txt

# Bootstrap cluster infrastructure (Sealed Secrets, cert-manager, NFS, Nvidia, Traefik)
ansible-playbook cluster_bootstrap.yml --vault-id fustercluck@passfile.txt

# Configure NFS server, Docker, Pi-hole on 20-size
ansible-playbook 20-size_config.yml --vault-id fustercluck@passfile.txt

# Create k8s secrets from vault-encrypted values
ansible-playbook create_secrets.yml --vault-id fustercluck@passfile.txt

# Deploy all applications via Helm (supports --tags media, plex, monitoring, homebridge, security)
ansible-playbook deploy_apps.yml --vault-id fustercluck@passfile.txt --tags media

# Encrypt a new secret value
ansible-vault encrypt_string --vault-id fustercluck@passfile.txt 'secret_value' --name 'var_name'

# Run against a specific host
ansible-playbook <playbook> -l kube01.local --vault-id fustercluck@passfile.txt

# Check mode (dry run)
ansible-playbook <playbook> --check --vault-id fustercluck@passfile.txt
```

## Cluster Topology

| Host | IP | Role | Special |
|------|----|------|---------|
| kube01.local | 10.0.1.30 | k3s control plane | Traefik ingress, klipper-lb |
| kube02.local | 10.0.1.50 | Worker | `homekit-bridge=true` label |
| kube03.local | 10.0.1.49 | Worker | `monitoring=true` label |
| 20-size.local | 10.0.1.203 | NFS server, GPU, media | `dedicated=media:NoSchedule` taint, Pi-hole |

## Deployment Order

For a fresh cluster:
1. `k3s_init.yml` — provision k3s cluster
2. `cluster_bootstrap.yml` — install infrastructure (Sealed Secrets, cert-manager, NFS provisioner, Nvidia, Traefik)
3. `20-size_config.yml` — configure NFS exports, Docker, Pi-hole on 20-size
4. `create_secrets.yml` — populate k8s secrets from vault
5. `deploy_apps.yml` — Helm install all applications

## Architecture

### Ansible Roles

- **common**: Baseline for all nodes — users, packages, `/etc/hosts` population (critical: k3s Go tools need static DNS, not mDNS), cgroups, libseccomp2 workaround
- **k3s**: Cluster install split into `server.yml`, `agent.yml`, `node_config.yml` (labels/taints), `kubeconfig.yml`
- **docker**: Docker CE install on 20-size (for Pi-hole container)
- **pihole**: Pi-hole v6 on Docker with macvlan (`lan` driver) — gets its own LAN IP (10.0.1.250), runs DHCP for the 10.0.1.0/24 subnet

### Why Pi-hole is not on k8s

Pi-hole runs as a Docker container on 20-size with a macvlan network rather than in k8s because: DNS is foundational (k8s failure would break DNS), port 53 conflicts with CoreDNS, and macvlan gives Pi-hole a real LAN IP without port-forwarding complexity. See `roles/pihole/PIHOLE.md` for details.

### Kubernetes Applications

All apps use the **bjw-s/app-template** OCI Helm chart (v3.6.1). App values live in `k3s/apps/`.

- **media**: nzbget, transmission, radarr, sonarr, lidarr, readarr, prowlarr, ombi
- **plex**: Plex (GPU-accelerated, pinned to 20-size via taint toleration + nodeSelector)
- **monitoring**: prometheus, grafana, influxdb, node-exporter (DaemonSet), kube-state-metrics, alloy (→ Grafana Cloud)
- **homebridge**: homebridge (hostNetwork=true, affinity to kube02)
- **security**: shinobi (video surveillance, pinned to 20-size)

### Storage

NFS exports from 20-size:
- `/snoqualmie/media` — media files
- `/seatac/plex` — Plex database/cache
- `/whidbey/configs` — application configs
- `/whidbey/backups` — backups

`nfs-subdir-external-provisioner` (StorageClass: `nfs-client`, Retain policy) auto-provisions PVCs as NFS subdirectories at `${namespace}/${pvc-name}`.

### Vault & Secrets

Vault ID is `fustercluck`, password stored in `passfile.txt` (not committed). Encrypted values live in `roles/*/vars/vault.yml` and inline in role vars. `create_secrets.yml` reads these and creates k8s Secrets. Exception: the Plex claim token is a one-time manual secret.

### Networking / DNS

Split-horizon DNS via Pi-hole:
- `*.alvani.me` resolves to 10.0.1.30 (kube01/Traefik) on LAN
- Other domains use Unlocator (185.37.37.37) + Cloudflare (1.1.1.1) upstream

TLS via cert-manager + Let's Encrypt using Cloudflare DNS-01 challenge. Cloudflare API token is stored as vault var and created as `cert-manager/cloudflare-api-token` secret.

### Global Variables

`vars/vars.yml` contains shared vars: `k3s_server_host`, `cluster_hosts` (static IP→hostname mappings), NFS paths, `common_users`.
