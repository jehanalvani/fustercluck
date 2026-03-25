# Kubernetes Infrastructure for k3s Home Lab

This directory contains all Kubernetes manifests and Helm values for the 4-node k3s cluster.

## Directory Structure

```
k8s/
├── infrastructure/          # Core cluster infrastructure
│   ├── namespaces.yml       # All cluster namespaces
│   ├── storage/             # Storage configuration
│   │   ├── nfs-storageclass.yml      # NFS provisioner values
│   │   ├── media-nfs-pv.yml          # Media NFS PV/PVC
│   │   ├── plex-nfs-pv.yml           # Plex storage volumes
│   │   └── prometheus-local-pv.yml   # Prometheus TSDB local storage
│   ├── cert-manager/        # Certificate management
│   │   ├── values.yml               # Helm values
│   │   └── cluster-issuer.yml       # ClusterIssuers and CA
│   ├── sealed-secrets/      # Sealed Secrets controller
│   │   └── values.yml               # Helm values
│   ├── nvidia/              # GPU support
│   │   └── device-plugin-values.yml # Nvidia device plugin values
│   └── traefik/             # Ingress controller
│       ├── config.yml               # HelmChartConfig for k3s Traefik
│       └── middleware.yml           # Traefik middlewares
├── secrets/                 # Secret management (git-excluded)
│   ├── README.md            # Sealed Secrets workflow guide
│   └── examples/            # Example secret templates
│       ├── nzbget-secret.example.yml
│       └── plex-secret.example.yml
└── README.md               # This file
```

## Cluster Topology

- **kube01.local** (control plane): k3s server
- **kube02.local**: homekit-bridge=true
- **kube03.local**: monitoring=true, TSDB at /mnt/tsdb
- **20-size.local**: NFS server, GPU node (dedicated=media taint)

## Deployment Order

### 1. Cluster Bootstrap (k3s installation)
Performed via infrastructure-as-code (Terraform/Ansible). Ensures:
- k3s is installed on all nodes
- Nodes are labeled and tainted as specified
- NFS server is configured with shares

### 2. Kubernetes Namespaces
Apply all namespaces first:
```bash
kubectl apply -f infrastructure/namespaces.yml
```

### 3. Storage Configuration
Deploy storage infrastructure:
```bash
# Storage classes and PVs
kubectl apply -f infrastructure/storage/

# Or helm install for NFS provisioner:
helm repo add nfs-subdir-external-provisioner https://kubernetes-sigs.github.io/nfs-subdir-external-provisioner/
helm install nfs-subdir-external-provisioner \
  nfs-subdir-external-provisioner/nfs-subdir-external-provisioner \
  -n kube-system \
  -f infrastructure/storage/nfs-storageclass.yml
```

### 4. Cert-Manager
Install cert-manager for TLS certificate management:
```bash
helm repo add jetstack https://charts.jetstack.io
helm install cert-manager jetstack/cert-manager \
  -n cert-manager \
  -f infrastructure/cert-manager/values.yml

# Deploy cluster issuers
kubectl apply -f infrastructure/cert-manager/cluster-issuer.yml
```

### 5. Sealed Secrets
Install sealed-secrets controller for secret encryption:
```bash
helm repo add sealed-secrets https://bitnami-labs.github.io/sealed-secrets
helm install sealed-secrets-controller sealed-secrets/sealed-secrets \
  -n sealed-secrets \
  -f infrastructure/sealed-secrets/values.yml
```

### 6. Traefik Configuration
Configure the built-in Traefik ingress controller:
```bash
kubectl apply -f infrastructure/traefik/
```

### 7. Nvidia Device Plugin (optional)
If using GPU on 20-size.local:
```bash
helm repo add nvidia https://nvidia.github.io/k8s-device-plugin
helm install nvidia-device-plugin nvidia/nvidia-device-plugin \
  -n kube-system \
  -f infrastructure/nvidia/device-plugin-values.yml
```

### 8. Applications
After infrastructure is ready, deploy application Helm charts (not in this directory).

## Adding a New Application

When adding a new application to the cluster:

1. **Create a namespace** (if needed) in `infrastructure/namespaces.yml`

2. **Create secrets** (if needed) using the Sealed Secrets workflow in `secrets/README.md`

3. **Prepare storage** (if needed):
   - Add PVC to `infrastructure/storage/` or use existing NFS provisioner
   - Configure volume mounts in app chart

4. **Deploy via Helm** using the [bjw-s app-template](https://bjw-s.github.io/helm-charts/):
   - Provides sensible defaults for common app patterns
   - Includes support for volumes, networking, security policies
   - Well-documented and widely used in home labs

Example using app-template:
```bash
helm install myapp bjw-s/app-template \
  -n media \
  --values myapp-values.yml
```

5. **Configure ingress** with Traefik annotation if exposing HTTP/HTTPS:
```yaml
ingress:
  enabled: true
  className: traefik
  annotations:
    traefik.ingress.kubernetes.io/router.middlewares: kube-system-https-redirect@kubernetescrd
  hosts:
    - host: myapp.kube01.local
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: myapp-tls
      hosts:
        - myapp.kube01.local
```

## Key Design Decisions

### Storage
- **NFS** for shared/large data (media, configs) - simple, reliable, sufficient for home lab
- **Local storage** for Prometheus TSDB - better performance for time-series queries
- **PV/PVC manifests** instead of StorageClass for simplicity in home lab with limited flexibility needs

### Networking
- **k3s Traefik** (built-in) instead of separate ingress controller - reduces resource consumption
- **Internal TLS** with self-signed CA - good enough for home lab, avoids Let's Encrypt complexity
- **Pod CIDR 10.5.0.0/16** - plenty of space for services and pods

### Security
- **Sealed Secrets** for runtime credentials - safe to commit, easy to backup and migrate
- **Ansible Vault** for infrastructure secrets - stays separate, encrypted at rest
- **Node taints** on GPU node - isolates workloads that need special resources

### Resource Constraints
All values are sized for a home lab with limited resources:
- Small resource requests/limits (50m CPU, 64Mi memory)
- Single replica for infrastructure components (replication not critical)
- Local storage over network where possible (better performance)

## Troubleshooting

### Check cluster health
```bash
kubectl get nodes -o wide
kubectl get pods -A
kubectl describe node kube03.local  # Check taints, labels, resources
```

### Verify storage
```bash
kubectl get pv
kubectl get pvc -A
kubectl describe pv prometheus-local-pv
```

### Test Traefik
```bash
# Port-forward to dashboard
kubectl port-forward -n kube-system svc/traefik 9000:9000
# Visit http://localhost:9000/dashboard
```

### Seal a secret
See `secrets/README.md` for complete Sealed Secrets workflow.

## References

- [k3s documentation](https://docs.k3s.io/)
- [Traefik v2](https://doc.traefik.io/traefik/)
- [cert-manager](https://cert-manager.io/)
- [Sealed Secrets](https://github.com/bitnami-labs/sealed-secrets)
- [bjw-s Helm charts](https://bjw-s.github.io/helm-charts/)
