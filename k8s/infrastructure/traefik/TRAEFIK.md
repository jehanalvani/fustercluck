# Traefik

Ingress controller shipped with k3s. Configured via `HelmChartConfig` (not a standalone Helm release).

## Access

- **Dashboard**: https://traefik.alvani.me

## Configuration

Managed by `config.yml` (a `HelmChartConfig` resource). Applied with:
```bash
kubectl apply -f k8s/infrastructure/traefik/config.yml
```

k3s watches this resource and reconciles Traefik automatically when it changes.

## Dashboard Setup

The dashboard requires two non-obvious settings:

1. **`api.dashboard: true`** in the HelmChartConfig — without this, `api@internal` is not registered as a TraefikService and the dashboard IngressRoute returns 404.
2. **`ingressRoute.dashboard.enabled: false`** — disables the built-in HTTP-only IngressRoute so our custom TLS one takes over.

The dashboard IngressRoute (`dashboard-ingress.yml`) must be applied **separately** — it is not part of the HelmChartConfig:
```bash
kubectl apply -f k8s/infrastructure/traefik/dashboard-ingress.yml
```

## TLS

All ingresses use cert-manager with the `letsencrypt-issuer` ClusterIssuer (DNS-01 via Cloudflare). The dashboard cert is managed by `dashboard-ingress.yml` which creates both the `Certificate` resource and the `IngressRoute`.

If the dashboard shows a **526 SSL error** from Cloudflare:
1. Check cert namespace — the `Certificate` and its `Secret` must be in `kube-system` (same namespace as the Traefik pod and IngressRoute)
2. Delete and re-apply: `kubectl delete certificate traefik-dashboard-tls -n kube-system && kubectl apply -f dashboard-ingress.yml`

## Node Routing (klipper-lb)

k3s uses klipper-lb (ServiceLB) to make the Traefik LoadBalancer service accessible on every node. The klipper DaemonSet only tolerates `node-role.kubernetes.io/master`, `node-role.kubernetes.io/control-plane`, and `CriticalAddonsOnly` — it does **not** tolerate `dedicated=media:NoSchedule`.

This means klipper-lb runs on kube01/kube02/kube03 but **not 20-size**. Any LAN DNS split-horizon entry (e.g. Pi-hole local DNS) must point to one of those three nodes, not to 20-size. Currently `pihole_ingress_ip = 10.0.1.30` (kube01).

## IngressRoute vs Ingress

Traefik supports both standard `Ingress` resources and its own `IngressRoute` CRDs (`traefik.io/v1alpha1`). App services use standard `Ingress`. The dashboard uses `IngressRoute` because it references `api@internal` which is a `TraefikService`, not a standard Kubernetes service.

## Middleware

`middleware.yml` defines reusable middleware (e.g. redirects, headers). Reference in an Ingress via annotation:
```yaml
traefik.ingress.kubernetes.io/router.middlewares: kube-system-<middleware-name>@kubernetescrd
```

## Prometheus Metrics

Metrics exposed on port 9100, scraped by Prometheus. Enabled via `metrics.prometheus.enabled: true` in the HelmChartConfig.
