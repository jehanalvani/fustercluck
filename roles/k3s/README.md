# k3s Role

This Ansible role installs and configures k3s (lightweight Kubernetes) across a cluster of nodes. It supports both server (control plane) and agent (worker) node configurations.

## Features

- Installs k3s server on designated control plane node
- Installs k3s agents on designated worker nodes
- Configures node labels and taints for workload targeting
- Distributes kubeconfig to system users
- Special handling for dedicated media node with GPU support and resource reservations
- ARM64 (aarch64) compatible

## Prerequisites

- All nodes must have the `common` role applied first
- k3s_server_host must be resolvable from all nodes (DNS or /etc/hosts)
- Nodes must support cgroups (enabled by common role)
- Network connectivity between all nodes on port 6443

## Role Variables

### Key Variables (defined in `vars/main.yml`)

- `k3s_version`: k3s version to install (default: v1.29.4+k3s1)
- `k3s_server_host`: hostname of control plane node (default: kube01.local)
- `k3s_server_port`: API server port (default: 6443)
- `k3s_pod_cidr`: Pod network CIDR (default: 10.5.0.0/16)
- `k3s_service_cidr`: Service network CIDR (default: 10.96.0.0/12)
- `k3s_node_config`: Dictionary of node-specific configurations (labels, taints)
- `k3s_nfs_node_kube_reserved`: Kubelet reserved resources for NFS server node
- `k3s_nfs_node_system_reserved`: System reserved resources for NFS server node

### Required Group Variables

Nodes must be in the following Ansible groups:
- `k3s_server`: control plane nodes
- `k3s_agent`: worker nodes

## Task Flow

1. **server.yml**: Install k3s server on control plane
   - Creates config directory
   - Templates server config
   - Runs k3s installation script
   - Waits for API server readiness
   - Fetches node token for agents

2. **agent.yml**: Install k3s agents on worker nodes
   - Creates config directory
   - Templates agent config (includes node-specific settings for 20-size.local)
   - Runs k3s installation script with server URL and token

3. **node_config.yml**: Apply labels and taints (runs on server)
   - Applies node labels for targeting specific workloads
   - Applies dedicated=media taint to restrict scheduling

4. **kubeconfig.yml**: Distribute kubeconfig (runs on server)
   - Creates ~/.kube directories for specified users
   - Copies and modifies kubeconfig for remote access
   - Sets appropriate file permissions

## Node-Specific Configuration

### 20-size.local (Media Node)
- Taint: `dedicated=media:NoSchedule` - only media workloads scheduled
- Labels: `node-role.kubernetes.io/media=true`, `nvidia.com/gpu=true`
- Resource reservations: CPU and memory reserved for NFS server processes

### kube02.local (Homekit Bridge)
- Label: `homekit-bridge=true` for targeting homekit-bridge workloads

### kube03.local (Monitoring)
- Label: `monitoring=true` for targeting monitoring infrastructure

## Usage

```yaml
# In k3s_init.yml or similar playbook

# Play 1: Common baseline
- hosts: k3s_cluster
  roles:
    - role: common

# Play 2: Install k3s server
- hosts: k3s_server
  roles:
    - role: k3s

# Play 3: Install k3s agents
- hosts: k3s_agent
  roles:
    - role: k3s
```

## Notes

- k3s runs containerd natively; no separate Docker installation needed
- Local storage is disabled in favor of nfs-subdir-external-provisioner
- Flannel vxlan backend provides cluster networking
- kubeconfig distributed to users specified in `common_users` variable
