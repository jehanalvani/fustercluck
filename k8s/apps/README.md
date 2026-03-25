# Kubernetes Applications

All applications are deployed using the [bjw-s/app-template](https://bjw-s.github.io/helm-charts) Helm chart, which provides a standardized, flexible template for container deployments.

## Quick Start

### Add the Helm Chart Repository

```bash
helm repo add bjw-s https://bjw-s.github.io/helm-charts
helm repo update
```

### Deploy a Single Application

```bash
helm upgrade --install <appname> bjw-s/app-template \
  --namespace <namespace> \
  --create-namespace \
  --values k8s/apps/<namespace>/<appname>/values.yml
```

Example - Deploy Sonarr:

```bash
helm upgrade --install sonarr bjw-s/app-template \
  --namespace media \
  --create-namespace \
  --values k8s/apps/media/sonarr/values.yml
```

## Application Organization

Applications are organized by namespace:

- **media**: Media server applications (Sonarr, Radarr, Lidarr, Readarr, NZBGet, Transmission, Prowlarr, Ombi)
- **plex**: Plex Media Server (GPU-enabled, pinned to media nodes)
- **monitoring**: Prometheus, Grafana, InfluxDB
- **homebridge**: HomeKit bridge (requires hostNetwork, pinned to specific node)
- **security**: Shinobi video surveillance system

## Adding a New Application

1. Create a new directory: `k8s/apps/<namespace>/<appname>/`
2. Copy an existing `values.yml` file as a template
3. Adjust configuration values:
   - Image repository and tag
   - Port numbers
   - Environment variables
   - Resource requests/limits
   - Storage configuration
4. Create any required secrets (see `k8s/secrets/README.md`)
5. Deploy using the helm command above

## Common Configuration Patterns

### Environment Variables

Standard environment variables used across media apps:

```yaml
TZ: America/Los_Angeles    # Timezone
PUID: "1002"               # Container user ID
PGID: "100"                # Container group ID
UMASK: "000"               # File creation mask (where applicable)
```

### Storage

- **Config Storage**: Uses `nfs-client` StorageClass with auto-provisioned NFS subdirectories
- **Media Storage**: Uses existing `media-nfs-pvc` claim (ReadWriteMany, mounted at /media)
- **PVC Sizes**: Adjusted per application needs; config typically 1-2Gi, data varies

### Networking

- **Ingress Class**: `traefik`
- **TLS**: Managed by cert-manager with `homelab-ca-issuer`
- **Host Pattern**: `<appname>.local`

## Prerequisites

Before deploying applications, ensure:

1. Kubernetes cluster is running with k3s or compatible distribution
2. Storage classes are available:
   - `nfs-client` for NFS auto-provisioning
   - `local-storage` (for local PVs, if used)
3. Ingress controller (Traefik) is installed
4. Cert-manager is installed with `homelab-ca-issuer` configured
5. Required PVCs exist (e.g., `media-nfs-pvc`, `plex-config-pvc`)
6. Required secrets are created (see `k8s/secrets/`)

## Monitoring & Troubleshooting

View pod status:

```bash
kubectl get pods -n <namespace>
```

View pod logs:

```bash
kubectl logs -n <namespace> <pod-name>
```

View resource usage:

```bash
kubectl top pods -n <namespace>
```

Check persistent volumes:

```bash
kubectl get pvc -n <namespace>
```

## Notes

- The `app-template` chart abstracts away most Kubernetes complexity, making configuration declarative and maintainable
- Each values file includes comments explaining non-obvious configuration choices
- Resource requests/limits are conservative estimates suitable for home lab deployments
- GPU support is configured for Plex (nvidia.com/gpu: 1)
- HostNetwork is used for Homebridge to enable HomeKit mDNS discovery
