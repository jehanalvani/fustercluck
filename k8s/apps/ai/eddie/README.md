# Eddie app values

bjw-s/app-template values for the Eddie cloud brain. Deployed by `deploy_eddie.yml`
onto the Hetzner k3s cluster (namespace `ai`).

| File | Component | Exposure |
|------|-----------|----------|
| `n8n.yml` | n8n — the automation engine (Eddie itself) | Public via Traefik (`n8n.alvani.me`) |
| `litellm.yml` | LiteLLM — model router (claude + open models) | ClusterIP |
| `qdrant.yml` | Qdrant — vector memory | ClusterIP |
| `searxng.yml` | SearXNG — private search | ClusterIP |

Full setup, DNS, secrets, and teardown: see [`/EDDIE.md`](../../../../EDDIE.md).

**Before applying:** pin the `latest` image tags (n8n, qdrant, searxng) to specific
releases for reproducible rebuilds.
