# Ombi

User-facing media request portal, deployed in the `media` namespace.

## Access

- **UI**: https://ombi.alvani.me

## Image

Uses `ghcr.io/hotio/ombi` — **not** `linuxserver/ombi`.

`linuxserver/ombi` exits immediately with help text because the `--host http://*:3579` startup argument is no longer valid. `hotio/ombi` runs cleanly and listens on **port 5000** (not 3579).

## Port

The service port is **5000**. If you ever switch back to linuxserver, the port would need to change.

## Probes

Ombi is a .NET app and takes ~60 seconds to initialize. Probes must have `initialDelaySeconds: 60` or the liveness probe will kill the pod before it's ready (exit code 137, which looks like OOM but is actually SIGKILL from the probe).

```yaml
probes:
  liveness:
    spec:
      initialDelaySeconds: 60
      failureThreshold: 5
```

## Persistence

Uses `existingClaim: ombi-config` — a pre-existing PVC. Not provisioned by Helm.

## Memory Limit

No memory limit is set intentionally. .NET apps have unpredictable GC behaviour and a hard limit causes OOMKill during garbage collection spikes. Monitor actual usage with `kubectl top pod -n media` and set a limit once baseline is established.

## SQLite

Ombi uses SQLite. If the database gets corrupted (symptom: `no such table` errors in logs), delete the `.db` files in the config PVC and restart — Ombi will re-initialize cleanly:

```bash
# Find the NFS path for ombi-config and wipe the db files
# Then:
kubectl rollout restart deployment/ombi -n media
```
