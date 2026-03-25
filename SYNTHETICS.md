# Grafana Cloud Synthetic Monitoring

End-to-end uptime and latency checks for all homelab services, visible in Grafana Cloud → Synthetic Monitoring.

## Architecture

Two probe types:

| Probe | Where it runs | What it can reach |
|---|---|---|
| **external** | Grafana public probe network | Public internet only — validates external reachability |
| **internal** | `homelab-private` k8s Deployment in `monitoring` ns | LAN + cluster services — validates local reachability |

Most *.alvani.me services are currently LAN-only. External checks will fail (expected) until services are publicly exposed. This gives a clear signal when external access is lost or gained.

## Label Taxonomy

All checks carry these labels for consistent filtering in dashboards and alerts:

| Label | Values | Purpose |
|---|---|---|
| `env` | `homelab` | Filter all homelab checks as a group |
| `tier` | `media`, `security`, `monitoring`, `network`, `infra` | Filter by service category |
| `service` | `sonarr`, `pihole`, `kube01`, etc. | Filter individual services |
| `probe` | `external`, `internal` | Compare public vs. LAN reachability |

## Check Types

### HTTP/HTTPS — cluster services (external + internal)
All 14 *.alvani.me services. Internal checks skip TLS verification (homelab CA not publicly trusted until cert-manager DNS-01 migration). `200`, `401`, `403` are valid — 401/403 expected after Traefik auth is added.

### DNS (internal only)
- `pihole-dns`: resolves `grafana.com` via Pi-hole (validates upstream forwarding)
- `pihole-local-dns`: resolves `traefik.alvani.me` via Pi-hole, validates answer is `10.0.1.30` (validates split-horizon)

### Ping (internal only)
All 4 cluster nodes + Pi-hole: kube01 (10.0.1.30), kube02 (10.0.1.50), kube03 (10.0.1.49), 20-size (10.0.1.203), pihole (10.0.1.250).

## Setup

### 1. Register the private probe

1. Grafana Cloud → Synthetic Monitoring → Private probes → **Add probe**
2. Name: `homelab-private`, region: `Other`
3. Copy the token (shown once)
4. Note the numeric probe ID shown in the probe list

### 2. Find external probe IDs

```bash
# List all available public probes (note: /api/v1/ path, not /sm/)
curl -s -H "Authorization: Bearer <sm_api_token>" \
  https://synthetic-monitoring-api-us-west-0.grafana.net/api/v1/probe/list | jq '.[] | {id, name, region}'
```

Current external probe IDs in use: **Oregon (15)**, **NorthCalifornia (18)**.

Pick a few geographically diverse probes (e.g. Atlanta, Frankfurt, Singapore).

### 3. Store tokens in Ansible vault

```bash
ansible-vault edit roles/common/vars/vault.yml
```

Add:
```yaml
sm_api_token: "your-sm-api-token"           # from SM → Config → API access
sm_api_url: "https://synthetic-monitoring-api.grafana.net"
sm_private_probe_id: 12345                  # numeric ID from probe list
sm_external_probe_ids:
  - 1    # e.g. Atlanta
  - 14   # e.g. Frankfurt
  - 24   # e.g. Singapore
```

The SM API token is separate from the Grafana Cloud API key. Get it from: Grafana Cloud → Synthetic Monitoring → Config → API access → Generate token.

### 4. Deploy the private probe agent

```bash
# Create the k8s secret
kubectl create secret generic sm-private-probe-secret \
  --from-literal=token='your-sm-probe-token' \
  -n monitoring

# Deploy
helm upgrade --install synthetic-monitoring bjw-s/app-template \
  --namespace monitoring \
  --values k8s/apps/monitoring/synthetic-monitoring/values.yml
```

### 5. Register all checks

```bash
ansible-playbook create_synthetics.yml
```

This reads `k8s/apps/monitoring/synthetic-monitoring/checks.yml` and creates/updates all checks via the SM API. Re-run any time `checks.yml` changes.

## Adding a new check

1. Add an entry to the appropriate section in `checks.yml`
2. Re-run `ansible-playbook create_synthetics.yml`

## Traefik auth impact

Once Traefik auth (forward auth / basic auth middleware) is added to services, external HTTP checks may start returning `401`. This is handled — `401` and `403` are in `validStatusCodes`, so checks won't fire false alerts. Internal checks can optionally be updated to include auth credentials once the auth mechanism is chosen.

## Plex external access

Plex external access has a known issue (tracked for investigation). The `plex-external` check will confirm whether port forwarding is currently working.

## Grafana Cloud Dashboards

After setup, checks appear under: **Grafana Cloud → Synthetic Monitoring → Checks**

For custom dashboards, query by label:
```
# All internal checks
probe_success{env="homelab", probe="internal"}

# Media services only
probe_success{env="homelab", tier="media"}

# Specific service, both probes
probe_success{env="homelab", service="sonarr"}
```
