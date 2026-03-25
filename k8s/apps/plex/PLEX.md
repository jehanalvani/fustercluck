# Plex Media Server

Media streaming server with NVIDIA GPU transcoding, deployed in the `plex` namespace on the `media` node (20-size).

## Access

- **UI**: https://plex.alvani.me
- **Direct**: http://10.0.1.203:32400/web (LAN only)

## Architecture

Runs on 20-size exclusively (nodeSelector + taint toleration for `dedicated=media`). Requires the NVIDIA device plugin DaemonSet to be healthy for GPU allocation.

| PVC | Mount | Purpose |
|-----|-------|---------|
| `plex-config-pvc` | `/config` | Plex database, metadata, preferences |
| `plex-transcode-pvc` | `/transcode` | Temporary transcode scratch space |
| `plex-media-pvc` | `/media` | NFS mount to `/snoqualmie/media` |

These are **pre-existing PVCs** (not provisioned by Helm). Defined in `k8s/infrastructure/storage/plex-nfs-pv.yml`.

## Image

Uses `linuxserver/plex` — **not** `plexinc/pms-docker`.

`plexinc/pms-docker` crashes with a fatal `libusb_init failed` error in this environment. `linuxserver/plex` suppresses it. Do not switch images.

## GPU

- `runtimeClassName: nvidia` on the pod
- `nvidia.com/gpu: "1"` resource limit
- `securityContext.privileged: true` required — without it the GPU device isn't accessible inside the container

### NVIDIA Device Plugin Dependency

The device plugin DaemonSet requires NFD (Node Feature Discovery) labels. NFD is not running in this cluster, so labels must be set manually on 20-size:

```bash
kubectl label node 20-size nvidia.com/gpu.present=true
kubectl label node 20-size feature.node.kubernetes.io/pci-10de.present=true
```

If the device plugin shows 0 desired pods, check these labels first.

## Probes

Default bjw-s app-template probes target the **first defined service port**. The service defines `http` (32400) and `dlna` (19989). Probes must be explicitly set to target port 32400 or they may hit the wrong port:

```yaml
probes:
  liveness:
    custom: true
    spec:
      tcpSocket:
        port: 32400
```

## Initial Setup (Claim Token)

`PLEX_CLAIM` is only needed once to register the server with plex.tv. After claiming, the env var can be removed from `values.yml`.

To get a new claim token: https://plex.tv/claim (tokens expire in 4 minutes).

Create the secret:
```bash
kubectl create secret generic plex-secret \
  --from-literal=PLEX_CLAIM='claim-XXXXX' \
  -n plex --dry-run=client -o yaml | kubectl apply -f -
```

> **Important**: run this as a single line. If the command wraps or is split across shell lines, the secret value gets a newline embedded and Plex silently ignores it.

## Traefik Note

Traefik cannot proxy the DLNA port (19989) via standard Ingress — it's only accessible on the LAN directly.
