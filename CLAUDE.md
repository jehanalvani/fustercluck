# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is a home lab infrastructure-as-code project using Ansible to provision and manage a 4-node k3s Kubernetes cluster plus supporting services (DNS, NFS, Docker). The cluster runs a media/monitoring/home-automation stack.

## Common Commands

`ansible.cfg` sets `vault_identity_list = fustercluck@passfile.txt`, so the `--vault-id` flag is optional for most playbooks (required only when `ansible.cfg` is not in scope, e.g. running from a different directory).

```bash
# Full cluster init (common → k3s server → agents → node labels/taints)
ansible-playbook k3s_init.yml --vault-id fustercluck@passfile.txt

# Bootstrap cluster infrastructure (Sealed Secrets, cert-manager, NFS, Nvidia, Traefik)
ansible-playbook cluster_bootstrap.yml --vault-id fustercluck@passfile.txt

# Configure NFS server, Docker, Pi-hole on 20-size
ansible-playbook 20-size_config.yml --vault-id fustercluck@passfile.txt

# Create k8s secrets from vault-encrypted values
ansible-playbook create_secrets.yml --vault-id fustercluck@passfile.txt

# Deploy HA config files (configuration.yaml, blueprints, packages, secrets) to NFS volume
# Run after editing any file under roles/homeassistant/files/, then reload HA YAML config
ansible-playbook create_ha_config.yml

# Deploy AI stack (Ollama, LiteLLM, Qdrant, SearXNG) on 20-size
ansible-playbook deploy_aistack.yml --vault-id fustercluck@passfile.txt

# Deploy all applications via Helm (supports --tags media, plex, monitoring, homeautomation, security)
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
| kube01.local | 10.0.1.30 | k3s control plane | Traefik ingress, klipper-lb, Pi-hole (Docker/macvlan, 10.0.1.250) |
| kube02.local | 10.0.1.50 | Worker | `homeautomation=true` label |
| kube03.local | 10.0.1.49 | Worker | `monitoring=true` label |
| 20-size.local | 10.0.1.203 | NFS server, GPU, media | `dedicated=media:NoSchedule` taint |

## Deployment Order

For a fresh cluster:
1. `k3s_init.yml` — provision k3s cluster
2. `cluster_bootstrap.yml` — install infrastructure (Sealed Secrets, cert-manager, NFS provisioner, Nvidia, Traefik)
3. `20-size_config.yml` — configure NFS exports, Docker, Pi-hole on 20-size
4. `create_secrets.yml` — populate k8s secrets from vault
5. `deploy_apps.yml` — Helm install all applications
6. `create_ha_config.yml` — deploy HA config files to NFS volume

## Architecture

### Ansible Roles

- **common**: Baseline for all nodes — users, packages, `/etc/hosts` population (critical: k3s Go tools need static DNS, not mDNS), cgroups, libseccomp2 workaround, static network config
- **k3s**: Cluster install split into `server.yml`, `agent.yml`, `node_config.yml` (labels/taints), `kubeconfig.yml`
- **docker**: Docker CE install on 20-size and kube01
- **pihole**: Pi-hole v6 on Docker with macvlan (`lan` driver) on **kube01** — gets its own LAN IP (10.0.1.250), runs DHCP for the 10.0.1.0/24 subnet. DHCP leases hand out the router (10.0.1.1) as secondary DNS fallback.
- **amcrest**: Amcrest camera config — NTP setup and vault-encrypted credentials for ONVIF/RTSP access
- **homeassistant**: HA deployment support — vault credentials (Netgear, Hue, OpenGarage) and git-managed config files under `files/`

### Why Pi-hole is not on k8s

Pi-hole runs as a Docker container on kube01 with a macvlan network rather than in k8s because: DNS is foundational (k8s failure would break DNS), port 53 conflicts with CoreDNS, and macvlan gives Pi-hole a real LAN IP without port-forwarding complexity. It was moved from 20-size because NFS lock events on 20-size caused host-level resource starvation that made FTL unresponsive. See `roles/pihole/PIHOLE.md` for details.

### Kubernetes Applications

All apps use the **bjw-s/app-template** OCI Helm chart (v3.6.1). App values live in `k8s/apps/`.

- **media**: nzbget, transmission, radarr, sonarr, lidarr, readarr, prowlarr, ombi
- **plex**: Plex (GPU-accelerated, pinned to 20-size via taint toleration + nodeSelector)
- **monitoring**: prometheus, grafana, influxdb, node-exporter (DaemonSet), kube-state-metrics, alloy (→ Grafana Cloud)
- **homeautomation**: home-assistant (hostNetwork=true, pinned to kube02). Config managed via `create_ha_config.yml` — see below.
- **security**: shinobi (video surveillance, pinned to 20-size)

### Home Assistant Config Management

HA config is split between git-managed files (deployed by `create_ha_config.yml`) and HA-owned files (written by the UI). Do not edit git-managed files directly on the server — they will be overwritten.

**Git-managed** (`roles/homeassistant/files/` → `/whidbey/configs/homeautomation/home-assistant-config/`):
- `configuration.yaml` — core config; enables `homeassistant.packages: !include_dir_named packages`
- `blueprints/automation/light_pico_remote.yaml` — Lutron Pico 5-button light remote blueprint
- `packages/light_remotes.yaml` — all Pico light remote automations
- `secrets.yaml` — Amcrest camera RTSP/snapshot URLs (rendered from vault)

**HA-owned** (not tracked in git):
- `automations.yaml` — UI-created automations (empty; git-managed ones live in packages/)
- `.storage/` — integration config, entity registry, device registry

**Workflow for adding/changing a git-managed automation:**
1. Edit the relevant file under `roles/homeassistant/files/`
2. `ansible-playbook create_ha_config.yml`
3. In HA: Developer Tools → YAML → All YAML configuration (or Reload Automations)

**Lutron Pico remote inventory:**

| Remote | Status | Primary Light | Secondary |
|--------|--------|---------------|-----------|
| Main Bedroom Jehan's Light Remote | ✓ automated | light.jehans_lamp | light.lindsays_lamp |
| Main Bedroom Lindsay's Light Remote | ✓ automated | light.lindsays_lamp | light.jehans_lamp |
| Front Porch Pico | ✓ automated | light.front_porch_lights | — |
| Garage Entry Hallway Light Pico | ✓ automated | light.garage_entry_main_lights | — |
| Stairwell Pico | ✓ automated | light.stairwell_light | — |
| Upstairs Hallway Laundry Side | ✓ automated | light.upstairs_hallway_main_lights | — |
| Upstairs Hallway Master Bedroom Door | ✓ automated | light.upstairs_hallway_main_lights | — |
| Office Office Remote | ✓ automated | light.office_lamp | — |
| Living Room Back Door | Lutron direct | light.living_room_overhead | — |
| Garage Entry GE Pico | HomeKit | switch.garage_lights | — |
| Living Room Pico (2-button) | HomeKit | switch.living_room_fireplace | — |
| Aubrey's Room Pico | ✓ automated | light.aubreys_lamp | stop = play/pause media_player.aubreys_room |
| Iain's Room Remote | Shade remote | — | — |
| Master Bathroom Remote | Shade remote | — | — |
| Play Room Remote | Shade remote (Lindsay's office) | — | — |
| Jehan's Audio Remote | Audio | — | — |
| Jehan's Shade Remote | Shades | — | — |
| Office Audio Pico | Unused | — | — |
| Kitchen Downstairs Remote | Lutron shade groups (FourGroupRemote) | — | — |

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

TLS via cert-manager with two issuers:
- `letsencrypt-issuer` — public certs via Let's Encrypt DNS-01 (Cloudflare). Token stored as vault var → `cert-manager/cloudflare-api-token` secret.
- `homelab-ca-issuer` — internal CA for `*.lan` services (Traefik dashboard, Pi-hole, etc.)

CoreDNS is patched via `k8s/infrastructure/coredns/` to forward `.lan` queries to Pi-hole (10.0.1.250), preventing NXDOMAIN from external resolvers.

### Global Variables

`vars/vars.yml` contains shared vars: `k3s_server_host`, `cluster_hosts` (static IP→hostname mappings), NFS paths, `common_users`.
