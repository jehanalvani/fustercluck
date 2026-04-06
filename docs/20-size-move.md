# Moving 20-size to Bedroom

20-size is the NFS server and media/GPU node. Nearly every app in the cluster depends on it.
Doing this without prep will hang pods cluster-wide (stale NFS mounts). Follow these steps in order.

## Prerequisites (done)

- [x] Static IP applied via netplan (`/etc/netplan/50-enp42s0-static.yaml`) — 10.0.1.203 no longer depends on DHCP
- [x] Bedroom network port confirmed on same VLAN (10.0.1.0/24)

## Step 1 — Scale everything down

No pods should be using NFS mounts when 20-size loses network. Do this first.

```bash
kubectl scale deployment -n media --all --replicas=0
kubectl scale deployment -n plex --all --replicas=0
kubectl scale deployment -n security --all --replicas=0
kubectl scale deployment -n homeautomation --all --replicas=0
```

Verify nothing is still running with NFS PVCs:
```bash
kubectl get pods -A | grep -v "kube-system\|monitoring\|Running\|Completed" 
```

## Step 2 — Drain and cordon the node

```bash
kubectl drain 20-size --ignore-daemonsets --delete-emptydir-data --timeout=60s
kubectl cordon 20-size
```

## Step 3 — Shut down gracefully

```bash
ansible 20-size.local -i inventory.yml -m shell -a "sudo shutdown -h now" --vault-id fustercluck@passfile.txt
```

Do **not** pull the power. Wait for it to fully power off before unplugging cables.

## Step 4 — Move, reconnect, boot

Connect the ethernet cable in the bedroom. Power on. Wait ~60 seconds.

## Step 5 — Verify it came back on the right IP

```bash
ping -c 3 10.0.1.203
ssh ansible@10.0.1.203 "hostname && ip addr show enp42s0 | grep inet"
```

If this fails, see Troubleshooting below before proceeding.

## Step 6 — Uncordon and scale back up

```bash
kubectl uncordon 20-size
kubectl scale deployment -n media --all --replicas=1
kubectl scale deployment -n plex --all --replicas=1
kubectl scale deployment -n security --all --replicas=1
kubectl scale deployment -n homeautomation --all --replicas=1
```

Spot-check that pods come up healthy:
```bash
kubectl get pods -A --watch
```

---

## Troubleshooting

### 20-size doesn't come back at 10.0.1.203

Static netplan is configured on `enp42s0`. If the machine doesn't respond:
- Confirm the bedroom port has link (check switch LEDs)
- Connect a monitor — look for kernel panic or network errors at boot
- If netplan is broken: boot with a USB, mount the drive, remove `/etc/netplan/50-enp42s0-static.yaml` to fall back to DHCP

### Pods are stuck after 20-size comes back

Stale NFS mounts cause pods to hang in I/O wait. If any pods got stuck before you scaled them down:

```bash
# Force delete stuck pods — they will reschedule
kubectl delete pod -n <namespace> <pod-name> --force --grace-period=0

# If the node itself shows NotReady
kubectl get nodes
kubectl describe node 20-size
```

### NFS mounts not working after 20-size is back

The NFS exports should survive a clean reboot. If PVCs stay in `Pending` or pods crash on startup:

```bash
# Check NFS exports are active on 20-size
ansible 20-size.local -i inventory.yml -m shell -a "sudo exportfs -v" --vault-id fustercluck@passfile.txt

# Restart the NFS provisioner
kubectl rollout restart deployment/nfs-subdir-external-provisioner -n kube-system
```

### Why last time failed (mid-day, no prep)

NFS mounts on kube01/02/03 went stale the moment 20-size lost network. Pods with active
NFS I/O hung indefinitely (hard mount behavior). The cluster couldn't evict or restart them,
requiring physical intervention on 20-size to restore network and unblock the hung mounts.
Scaling to 0 first eliminates this entirely.
