# Helm Values Files - Application Index

This directory contains complete Helm values files for all home lab applications migrated to k3s using the bjw-s/app-template chart.

## Quick Navigation

### Media Namespace (`media`)

| Application | Port | Purpose | Config Size | Special Notes |
|---|---|---|---|---|
| [nzbget](media/nzbget/values.yml) | 6789 | Usenet client | 1Gi | Intermediate: 10Gi — see [README](media/nzbget/README.md) |
| [transmission](media/transmission/values.yml) | 9091 | BitTorrent client | 1Gi | Torrent: 51413 (NodePort) |
| [radarr](media/radarr/values.yml) | 7878 | Movie management | 2Gi | Integrates with all *arr apps |
| [sonarr](media/sonarr/values.yml) | 8989 | TV show management | 2Gi | Integrates with all *arr apps |
| [lidarr](media/lidarr/values.yml) | 8686 | Music management | 2Gi | Integrates with all *arr apps |
| [readarr](media/readarr/values.yml) | 8787 | Book management | 2Gi | Uses develop tag |
| [prowlarr](media/prowlarr/values.yml) | 9696 | Indexer manager | 1Gi | No media mount |
| [ombi](media/ombi/values.yml) | 3579 | Request manager | 1Gi | No media mount |

### Plex Namespace (`plex`)

| Application | Port | Purpose | Special Configuration |
|---|---|---|---|
| [plex](plex/values.yml) | 32400 | Media server | GPU enabled, pinned to media node |

**Key Features:**
- NVIDIA GPU support (`nvidia.com/gpu: "1"`)
- `runtimeClassName: nvidia`
- Node selector: `node-role.kubernetes.io/media: "true"`
- Tolerates `dedicated=media:NoSchedule`
- PLEX_CLAIM secret for initial registration

### Monitoring Namespace (`monitoring`)

Visualization, storage, and alerting handled by **Grafana Cloud** (grafana.com).
In-cluster components are collectors only — minimal resource footprint.

| Application | Port | Purpose | Runs On |
|---|---|---|---|
| [node-exporter](monitoring/node-exporter/values.yml) | 9100 | Host metrics (CPU, memory, disk, network) | DaemonSet (all nodes) |
| [kube-state-metrics](monitoring/kube-state-metrics/values.yml) | 8080 | Cluster state (pod restarts, resource requests) | Any node |
| [alloy](monitoring/alloy/values.yml) | — | Ships metrics + logs to Grafana Cloud | DaemonSet (all nodes) |
| [synthetic-monitoring](monitoring/synthetic-monitoring/values.yml) | — | Private probe agent for Grafana Cloud SM | Single pod, any node |

**Architecture: Custom Alloy + Grafana Cloud Kubernetes Integration**

We use a custom Alloy config (not the `grafana/k8s-monitoring` Helm chart) to retain control
over the log filtering and credential redaction pipeline. The Grafana Cloud Kubernetes Integration
dashboards (Observability → Kubernetes) are activated by matching its expected data shape:

- `cluster="homelab"` external label on all metrics (set in `prometheus.remote_write`)
- `job="integrations/kubernetes/eventhandler"` on cluster events (via `loki.source.kubernetes_events`)
- Standard scrape jobs: node-exporter, kube-state-metrics, kubelet, cAdvisor

**Do not** deploy `grafana/k8s-monitoring` chart — it would duplicate collectors and use fleet
management (remote config push) which conflicts with our local config approach.

**Alloy pipeline:**
- Metrics: node-exporter, kube-state-metrics, kubelet, cAdvisor → Grafana Cloud Mimir
- Pod logs: all namespaces via `local.file_match` → `/var/log/pods/*/*/*.log` → filter/redact → Grafana Cloud Loki
- Host journal logs: `loki.source.journal` on every node — k3s, kubelet, SSH, kernel events. Query: `{job="host/journal", node="<nodename>"}`
- Docker container logs: `loki.source.docker` on 20-size only (Pi-hole + any other Docker containers). Query: `{job="docker", container="pihole"}`
- Events: Kubernetes cluster events (OOM kills, scheduling failures) → Grafana Cloud Loki
- WAL persisted at `/var/lib/alloy` (hostPath) — survives pod restarts and network blips
- Credentials in `grafana-cloud-secret` (see `k8s/secrets/examples/grafana-cloud-secret.example.yml`)
- Alloy tolerates all NoSchedule taints — runs on all 4 nodes including 20-size

**Helm param note:** Use `controller.volumes.extra` / `alloy.mounts.extra` / `controller.tolerations` — `extraVolumes`/`extraVolumeMounts` are silently ignored by this chart.

**Grafana Cloud Dashboards:**
- Kubernetes Health/Clusters/Nodes/Workloads: Observability → Kubernetes (built-in, no import needed)
- Node Exporter Full: import ID `1860`
- k3s Cluster Monitoring: import ID `16450` (datasource: `grafanacloud-alvani-prom`)

**Troubleshooting:**
- No metrics: `kubectl logs -n monitoring -l app.kubernetes.io/name=alloy --tail=20 | grep 401`
- No logs: `kubectl logs -n monitoring -l app.kubernetes.io/name=alloy --tail=20 | grep -i loki`
- Config errors: `kubectl logs -n monitoring -l app.kubernetes.io/name=alloy --tail=20 | grep error`
- Clear log position cache: `kubectl get pods -n monitoring -l app.kubernetes.io/name=alloy -o name | xargs -I{} kubectl exec -n monitoring {} -- sh -c 'rm -rf /var/lib/alloy/data/loki*'`
- Token lives in `grafana-cloud-secret` → rotate via Grafana Cloud Access Policies → `kubectl delete secret grafana-cloud-secret -n monitoring && kubectl create secret generic grafana-cloud-secret --from-literal=... -n monitoring`
- Stale Helm jobs showing as "not ready": `kubectl delete job helm-install-traefik helm-install-traefik-crd -n kube-system`

### Homebridge Namespace (`homebridge`)

| Application | Port | Purpose | Special Configuration |
|---|---|---|---|
| [homebridge](homebridge/values.yml) | 8080 | HomeKit bridge | hostNetwork, kube02 node |

**Key Features:**
- `hostNetwork: true` (required for mDNS)
- `dnsPolicy: ClusterFirstWithHostNet`
- Node selector: `homekit-bridge: "true"`
- HomeKit pairing port: 51826

### Security Namespace (`security`)

| Application | Port | Purpose | Special Configuration |
|---|---|---|---|
| [shinobi](security/shinobi/values.yml) | 8080 | Video surveillance | Multi-container, MySQL sidecar |

**Key Features:**
- MySQL 8 sidecar container
- Pinned to media node with `dedicated=media:NoSchedule` taint
- 500Gi recordings PVC (NFS default)
- Secret-based database initialization

## Common Patterns

### Standard Environment Variables (Media Apps)

All media applications use:
```yaml
TZ: America/Los_Angeles
PUID: "1002"
PGID: "100"
UMASK: "000"
```

### Storage Configuration

**Config Storage (nfs-client):**
- Auto-provisioned from `/whidbey/configs`
- ReadWriteOnce access mode
- Typical sizes: 1-2Gi per app

**Media Storage (media-nfs-pvc):**
- Shared NFS mount at `/snoqualmie/media`
- ReadWriteMany (shared across all media apps)
- Required PVC must exist before deployment

### Ingress Pattern

All applications follow this pattern:
```yaml
ingress:
  main:
    className: traefik
    annotations:
      cert-manager.io/cluster-issuer: homelab-ca-issuer
    hosts:
      - host: <appname>.local
        paths:
          - path: /
            pathType: Prefix
            service:
              identifier: main
              port: http
    tls:
      - secretName: <appname>-tls
        hosts:
          - <appname>.local
```

## Resource Allocation Guidelines

| Category | CPU Requests | CPU Limits | Memory Requests | Memory Limits |
|---|---|---|---|---|
| Minimal (prowlarr, ombi) | 50m | 200m | 128-256Mi | 256-512Mi |
| Standard (media, grafana) | 100m | 500m | 256Mi | 512Mi |
| Heavy (prometheus) | 200m | 1000m | 512Mi | 2Gi |
| Large (plex) | 500m | 4000m | 1Gi | 4Gi |

## Prerequisites Checklist

Before deploying any applications:

- [ ] Kubernetes cluster (k3s) operational
- [ ] Helm installed and updated
- [ ] bjw-s chart repo added: `helm repo add bjw-s https://bjw-s-labs.github.io/helm-charts`
- [ ] `nfs-client` StorageClass configured
- [ ] Required PVCs created:
  - [ ] `media-nfs-pvc` (ReadWriteMany, /snoqualmie/media)
  - [ ] `plex-config-pvc`, `plex-transcode-pvc`, `plex-media-pvc`
- [ ] Required secrets created (see examples in `/k8s/secrets/examples/`)
- [ ] Required namespaces created:
  - [ ] `media`
  - [ ] `plex`
  - [ ] `monitoring`
  - [ ] `homebridge`
  - [ ] `security`
- [ ] Traefik ingress controller installed
- [ ] cert-manager installed with `homelab-ca-issuer`
- [ ] Node labels and taints configured:
  - [ ] `homekit-bridge: "true"` on kube02
  - [ ] `node-role.kubernetes.io/media: "true"` on media node
  - [ ] `monitoring: "true"` on kube03
  - [ ] `dedicated=media:NoSchedule` taint on 20-size
- [ ] NVIDIA GPU and device plugin configured (for Plex)

## Deployment Examples

### Deploy Single Application
```bash
helm upgrade --install sonarr bjw-s/app-template \
  --version 3.6.1 \
  --namespace media \
  --create-namespace \
  --values k8s/apps/media/sonarr/values.yml
```

### Deploy All Media Applications
```bash
for app in nzbget transmission radarr sonarr lidarr readarr prowlarr ombi; do
  helm upgrade --install $app bjw-s/app-template \
    --version 3.6.1 \
    --namespace media \
    --create-namespace \
    --values k8s/apps/media/$app/values.yml
done
```

### Deploy Monitoring Stack
```bash
# Add required repos first (one-time)
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add grafana https://grafana.github.io/helm-charts
helm repo update

# Create secret before deploying alloy (see k8s/secrets/examples/grafana-cloud-secret.example.yml)
# kubectl create secret generic grafana-cloud-secret --from-literal=... -n monitoring

helm upgrade --install node-exporter prometheus-community/prometheus-node-exporter \
  --namespace monitoring --values k8s/apps/monitoring/node-exporter/values.yml

helm upgrade --install kube-state-metrics prometheus-community/kube-state-metrics \
  --namespace monitoring --values k8s/apps/monitoring/kube-state-metrics/values.yml

helm upgrade --install alloy grafana/alloy \
  --namespace monitoring --values k8s/apps/monitoring/alloy/values.yml
```

### Verify Deployment
```bash
# Check pod status
kubectl get pods -n media

# View logs
kubectl logs -n media -l app.kubernetes.io/name=sonarr

# Check resource usage
kubectl top pods -n media

# View persistent volumes
kubectl get pvc -n media
```

## Secret Management

See `/k8s/secrets/examples/` for template files:

- `grafana-secret.example.yml` - Admin password
- `influxdb-secret.example.yml` - Admin password
- `shinobi-db-secret.example.yml` - MySQL credentials
- `plex-secret.example.yml` - PLEX_CLAIM token (temporary)
- `nzbget-secret.example.yml` - (pre-existing)

Create actual secrets:
```bash
kubectl create secret generic grafana-secret \
  --from-literal=admin-password='your-password' \
  -n monitoring
```

## Troubleshooting

### Pod won't start
```bash
# Check pod events
kubectl describe pod <pod-name> -n <namespace>

# Check logs
kubectl logs <pod-name> -n <namespace>

# Check PVC status
kubectl get pvc -n <namespace>
```

### Storage issues
```bash
# Check PV/PVC status
kubectl get pv,pvc

# Check storage class
kubectl get storageclass

# Check NFS mounts
kubectl exec -it <pod-name> -n <namespace> -- df -h
```

### Networking issues
```bash
# Check ingress
kubectl get ingress -n <namespace>

# Check service
kubectl get svc -n <namespace>

# Test DNS
kubectl run -it --rm debug --image=busybox --restart=Never -- \
  nslookup sonarr.local
```

## Notes

- All files use bjw-s/app-template v3 structure
- Complete YAML validation performed
- All configurations extracted from existing ansible vars files
- No placeholder values - ready for immediate deployment
- Comments explain non-obvious configuration choices
- Resource allocations are home-lab appropriate
- Special hardware/networking requirements clearly documented

## Additional Resources

- [bjw-s App Template Chart](https://bjw-s.github.io/helm-charts)
- [Helm Documentation](https://helm.sh/docs/)
- [Kubernetes Documentation](https://kubernetes.io/docs/)
- See [README.md](README.md) for general deployment information
