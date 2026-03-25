# Improvement Options

## External Access & Privacy

### Current state
Orbi forwards ports 80/443 → kube01 (10.0.1.30). Cloudflare proxies `*.alvani.me` → home public IP (orange cloud). Home IP is visible to Cloudflare.

### Option A: Cloudflare Tunnel
- No open ports on Orbi at all
- A daemon (`cloudflared`) runs in the cluster and dials out to Cloudflare
- Inbound traffic proxied through that tunnel
- Home IP never exposed to the public internet
- **Tradeoff:** Cloudflare terminates TLS — they can see plaintext traffic
- Free tier

### Option B: Tailscale (VPN-only, no public exposure)
- Close Orbi port forwarding entirely
- Install Tailscale on cluster nodes and client devices
- All access via WireGuard mesh (`100.x.x.x` addresses)
- Traffic is end-to-end encrypted — Tailscale sees connection metadata but not content
- `*.alvani.me` DNS points at Tailscale IPs instead of public IP
- **Tradeoff:** Must have Tailscale running on every device you want access from

### Option C: Headscale (self-hosted Tailscale control plane)
- Same WireGuard mesh as Tailscale but control plane runs on your own infra
- Zero third-party involvement
- More operational overhead — you run the coordination server
- **Tradeoff:** Another thing to maintain

### Recommendation
If hiding home IP is the priority and you want the simplest path: **Tailscale**.
If you want zero third-party trust: **Headscale** on a cheap VPS.
Cloudflare Tunnel is a step up from current but still puts Cloudflare in the middle.

---

## Pi-hole Per-Client DNS Visibility

### Current state
Orbi always hands out its own IP as the DNS server via DHCP. All Pi-hole queries appear to come from the router, not individual clients.

### Option A: Pi-hole DHCP (recommended)
- Disable Orbi DHCP: **Advanced > Setup > LAN Setup** → uncheck "Use Router as DHCP Server"
- Enable Pi-hole DHCP (Settings > DHCP), set range to match subnet
- Pi-hole hands out its own IP as DNS → sees real client IPs in logs
- **Tradeoff**: If Pi-hole pod restarts, DHCP is unavailable until it recovers

### Option B: Per-device static DNS
- Manually set DNS to Pi-hole's IP on specific devices you want to track
- Pi-hole sees real IPs only for those devices
- **Tradeoff**: Not scalable; new devices default to Orbi DNS

### Option C: Voxel custom firmware
- Flash Orbi with Voxel firmware, modify DHCP option 6 via startup scripts
- Only tested on older RBK30; risky and fragile
- **Tradeoff**: Not worth it given Option A exists

### Recommendation
**Option A** — Pi-hole DHCP is stable and purpose-built for this. No meaningful redundancy lost in a home setup.

---

## NFS Mount Stability (Recurring Outage Root Cause)

### Problem
All NFS mounts cluster-wide use `hard` mount option (kubelet default). When the NFS server (20-size) becomes unavailable or sluggish — which happens when 20-size has kernel-level cgroup deadlocks from broken containerd shims — all `mount.nfs` calls on worker nodes hang in D state indefinitely. This cascades to block pod scheduling and makes nodes unresponsive.

20-size is simultaneously:
- The primary compute node (Plex, Shinobi, GPU workloads)
- The NFS server for all cluster storage (`/whidbey/configs`, `/snoqualmie/media`, `/seatac/plex`)

A cgroup deadlock on 20-size (triggered by force-deleting pods with broken container namespaces) degrades the NFS server, which blocks all worker nodes.

### Option A: Change NFS mounts to `soft` with timeout
Add `soft,timeo=600,retrans=3` to nfs-client StorageClass `mountOptions`. Soft mounts return ESTALE after timeout instead of hanging forever.
- **Risk**: If NFS is temporarily slow (not down), soft mounts can cause I/O errors on running pods
- **Tradeoff**: Pods may crash during NFS blips rather than hang indefinitely

### Option B: `hard,intr` (interruptible hard mounts)
Already the default on modern kernels — `intr` allows SIGKILL to interrupt a D-state NFS wait. Verify kube02/kube03 kernel version supports it.
- Allows `kill -9` on stuck `mount.nfs` processes without needing a reboot

### Option C: Move NFS server off 20-size (recommended long-term)
Run NFS on a dedicated lightweight node (kube01 or a Pi) or use a NAS with its own NFS service. Decouples storage availability from compute node health.

### Option D: PodDisruptionBudget + pre-drain hook
Before force-deleting pods on 20-size, drain the node first (`kubectl drain 20-size --ignore-daemonsets`). This lets kubelet cleanly unmount NFS volumes before removing pods.

### Known trigger: seatac pool not imported
`seatac` is an SSD ZFS mirror (`sda1`/`sdb1`) that houses Plex config/transcode PVCs and the nzbget intermediate path. If it's not imported on boot, the NFS server silently fails to serve those paths, which cascades to NFS timeouts cluster-wide. It was added to `/etc/zfs/zpool.cache` and `zfs-import-cache.service` is enabled — it should auto-import. If it doesn't, run `sudo zpool import -f seatac` on 20-size.

### Known trigger: stale PID files after forced pod deletion
If a pod on 20-size is force-deleted while NFS is hung (the usual recovery procedure), the app's PID file in `/whidbey/configs/media/<app>-config/<app>.pid` survives. On the next start the app detects the stale PID, thinks another instance is running, and immediately exits — causing a CrashLoopBackOff with no error in the logs. Fix: delete the `.pid` files in the config directories for any app in a crash loop after a forced restart. Known affected apps: radarr, sonarr, lidarr, readarr, prowlarr.

### Immediate fix (temporary mitigation)
Update nfs-client StorageClass with `mountOptions: [hard, intr, timeo=600]` to allow killing stuck mounts without a full reboot. This is a band-aid — the real fix is Option C below.

### Long-term fix
20-size was originally intended as a dedicated NAS but has become a hybrid NAS/compute node (Plex, Shinobi, GPU workloads, NFS server all on the same machine). The coupling between compute instability and storage availability is the root cause of these cascading failures.

**Move NFS to a dedicated node** (kube01/kube03 as NFS server, or a separate small box) and use 20-size purely for compute. Until then, every cgroup deadlock on 20-size takes down storage for the entire cluster.

---

## Shinobi Memory and FFmpeg Stability (Resolved)

### Problem
Shinobi was OOMKilled regularly. Three contributing causes:

1. **Memory limit too low**: 5 concurrent camera streams consumed ~4.1GB RSS (ffmpeg + node + mariadbd), exceeding the 4Gi container limit.
2. **x264 re-encoding**: Backyard and Driveway monitors had `vcodec: none`, which defaults to libx264 re-encoding. Amcrest H.264 streams caused `libx264.so` segfaults every ~45 seconds on one stream, creating a crash/restart loop and adding to memory pressure.
3. **postStart race**: The init container wrote the static FFmpeg 7.x binary to `/ffmpeg-bin`, but the main container used a `lifecycle.postStart` cp hook to put it in `/usr/bin/ffmpeg`. If Shinobi started camera streams before postStart completed, the bundled FFmpeg 4.3.9 (which segfaults on Amcrest multi-slice H.264) handled those streams.

### Fixes applied
- Memory limit raised from 4Gi to 6Gi (`k8s/apps/security/shinobi/values.yml`)
- All Shinobi monitors set to `vcodec: copy` — ffmpeg remuxes RTSP without transcoding. See `CAMERAS.md` for why this must not be changed.
- postStart cp hook removed; `PATH=/ffmpeg-bin:...` added to container env so the static binary is used from the first instant without a race window.

---

## Transmission Seeding Configuration (Resolved)

### Strategy: Option C — keep torrent in queue after import, relabel to `*-seeding`
Radarr v4 removed per-download-client seed ratio/time fields. Seeding is now managed entirely by Transmission.

### Configuration applied
- **Transmission** (`k8s/apps/media/transmission/values.yml`): postStart lifecycle hook waits for Transmission RPC on port 9091, then calls `session-set` with `seedRatioLimit=1.0`, `seedRatioLimited=true`, `idle-seeding-limit=65535` (max uint16 ≈45 days — Transmission's ceiling; 1 year overflows to 1312), `idle-seeding-limit-enabled=true`. Fires on every pod restart to counteract LSIO's reset.
- **Radarr**: `removeCompletedDownloads=false`, `movieImportedCategory=radarr-seeding`
- **Sonarr**: `removeCompletedDownloads=false`, `tvImportedCategory=sonarr-seeding`
- **Lidarr**: `removeCompletedDownloads=false`, `musicImportedCategory=lidarr-seeding`
- **Readarr**: `removeCompletedDownloads=false` only — no imported category field in Readarr's Transmission client schema

After arr app imports a file, the torrent is relabeled to `*-seeding` in the Transmission queue. Torrents seed until Transmission's global ratio (1.0) or idle time (1 year) limit is reached, then Transmission removes them automatically.

---

## Pending / Nice to Have

- Add `vault_cloudflare_api_token` and `pihole_web_password` to Ansible Vault (currently hardcoded in `roles/pihole/vars/main.yml`)
- Grafana dashboards wired to Prometheus + InfluxDB
- Node resource limits review (prowlarr/ombi have no memory limits due to .NET OOM issues)
- **Prowlarr arr integration**: connect Sonarr, Radarr, Lidarr, Readarr to Prowlarr via Settings > Apps in each app
- **NZBGet download client**: add NZBGet as download client in Radarr, Sonarr, Lidarr, Readarr; host `nzbget`, port `6789`, category per-app
- **Email/SMTP configuration**: wire up notification email settings on all services — Plex, Ombi, Prowlarr, Shinobi, Grafana (use a shared SMTP secret/ConfigMap so credentials aren't duplicated across values files)
- **Homebridge config portability**: once UI config is done, extract `config.json` from PVC, add to repo as template, wire into deployment as ConfigMap/init so it survives redeploys
  ```bash
  kubectl exec -n homebridge deploy/homebridge -- cat /homebridge/config.json
  ```
