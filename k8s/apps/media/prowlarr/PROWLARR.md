# Prowlarr

Indexer manager for the Arr suite (Sonarr, Radarr, Lidarr, Readarr). Deployed in the `media` namespace.

## Access

- **UI**: https://prowlarr.alvani.me

## Image

Uses `ghcr.io/hotio/prowlarr`. Previously `linuxserver/prowlarr` — switched during initial setup to resolve probe/startup issues.

## Probes

Prowlarr is a .NET app. Same as Ombi, requires `initialDelaySeconds: 60` on probes to avoid premature liveness kills during initialization.

## Persistence

Uses `existingClaim: prowlarr-config` — a pre-existing PVC. Not provisioned by Helm.

## Memory Limit

No memory limit set intentionally — same reasoning as Ombi (.NET GC spikes). Monitor with `kubectl top pod -n media`.

## Role in the Stack

Prowlarr is the central indexer hub. Configure indexers here once and sync to all Arr apps via the Prowlarr API integration rather than configuring indexers in each app individually.
