# Shinobi

Video surveillance system deployed in the `security` namespace on the `media` node (20-size).

## Access

- **UI**: https://shinobi.alvani.me
- **Super admin**: https://shinobi.alvani.me/super (default creds: `admin@shinobi.video` / `admin` — change immediately)

## Architecture

Single container image (`registry.gitlab.com/shinobi-systems/shinobi`) with **MariaDB built in**. The app and DB both run in the same pod.

| PVC | Mount | Purpose |
|-----|-------|---------|
| `shinobi-config` | `/config` | App config + cached static FFmpeg binary |
| `shinobi-recordings` | `/recordings` | Video recordings (500Gi) |
| `shinobi-db` | `/var/lib/mysql` | MariaDB data |

## FFmpeg Override

The bundled Shinobi image ships **FFmpeg 4.3.9 (Debian Bullseye)**, which segfaults (exit 139) on H.264 multi-slice streams from Amcrest cameras (`top block unavailable for requested intra mode`). This is a known FFmpeg bug fixed in 5.x+.

The deployment includes an **init container** that installs a static FFmpeg 7.x build from johnvansickle.com:

1. On first run, downloads `ffmpeg-release-amd64-static.tar.xz` and writes the binary to `/config/ffmpeg` (persisted to the config PVC)
2. On subsequent restarts, copies from `/config/ffmpeg` (skips download)
3. A `postStart` lifecycle hook copies the binary to `/usr/bin/ffmpeg` before Shinobi starts

Shinobi hardcodes `/usr/bin/ffmpeg` — PATH overrides are not sufficient.

## DB Setup Callout

The Shinobi image init scripts **ignore** the `MYSQL_USER`, `MYSQL_PASSWORD`, and `MYSQL_DATABASE` env vars. On a fresh DB volume it initializes with its own defaults (`majesticflame` user, `ccio` database).

The `DB_*` env vars in `values.yml` configure what the **app** connects to, not what MariaDB creates. On first run these must be reconciled manually:

```bash
kubectl exec -n security deployment/shinobi -- bash -c "
  mysql -u root -p\$MYSQL_ROOT_PASSWORD -e \"
    CREATE DATABASE IF NOT EXISTS shinobi;
    CREATE USER IF NOT EXISTS 'shinobi'@'localhost' IDENTIFIED BY '\$DB_PASSWORD';
    GRANT ALL PRIVILEGES ON shinobi.* TO 'shinobi'@'localhost';
    FLUSH PRIVILEGES;
  \"
"
```

Then restart the deployment so Shinobi runs its schema migrations against the new database.

## Helm Upgrade / Service Selector Issue

The bjw-s/app-template chart uses `app.kubernetes.io/component=main` in the pod labels and service selector. If the pod was previously deployed with `app.kubernetes.io/controller=main` (a label key used in some chart versions), the service will have no endpoints and traffic won't route.

**Symptom**: Shinobi UI returns 404. Pod is running and `kubectl exec -- curl localhost:8080` returns 200, but `kubectl get endpoints -n security` shows `<none>`.

**Fix**: Patch the service selector to match the actual pod labels:
```bash
kubectl patch svc shinobi -n security -p '{"spec":{"selector":{"app.kubernetes.io/component":null,"app.kubernetes.io/controller":"main"}}}'
# or the reverse, depending on what the pods have:
kubectl get pod -n security --show-labels
```

If the deployment selector itself is wrong (immutable field error on helm upgrade), delete the deployment and re-run helm upgrade. PVCs survive.

## Amcrest Camera Streams

All three Amcrest cameras share the same RTSP path format:

```
rtsp://stream:<pass>@<camera-ip>:554/cam/realmonitor?channel=1&subtype=0  # main stream
rtsp://stream:<pass>@<camera-ip>:554/cam/realmonitor?channel=1&subtype=1  # sub stream (low res)
```

Monitors use `auto_host_enable=1` so the full URL (including credentials) from the `auto_host` field is passed directly to FFmpeg. If streams show 401 errors, verify this is set:

```bash
kubectl exec -n security deployment/shinobi -- \
  mysql -u shinobi -p<DB_PASSWORD> shinobi \
  -e "SELECT mid, name, JSON_EXTRACT(details, '$.auto_host_enable'), JSON_EXTRACT(details, '$.auto_host') FROM Monitors;"
```

### The DTS Timestamp Fix

Amcrest cameras send **non-monotonically increasing DTS timestamps** in the main stream. Shinobi's FFmpeg pipeline dies immediately ("Died" status) when it encounters these.

**Every monitor must have this set in `cust_input` (Extra Input Options):**
```
-fflags +genpts+discardcorrupt
```

Set via DB if needed:
```bash
kubectl exec -n security deployment/shinobi -- \
  mysql -u shinobi -p<DB_PASSWORD> shinobi \
  -e "UPDATE Monitors SET details = JSON_SET(details, '$.cust_input', '-fflags +genpts+discardcorrupt');"
```

### Image Orientation

Front Porch is mounted upside-down and mirrored. These are configured on the camera via CGI (stored in `inventory.yml` as `amcrest_image_flip: true` / `amcrest_image_mirror: true`) and applied by the `amcrest` Ansible role (`--tags image`).

### Known Cameras

| Name | IP | Notes |
|------|----|-------|
| Backyard | 10.0.1.157 | cust_input fix applied |
| Front Porch | 10.0.1.163 | cust_input fix applied; flip + mirror enabled on camera |
| Driveway | 10.0.1.164 | cust_input fix applied |

## Useful Commands

```bash
# Check logs
kubectl logs -n security deployment/shinobi --tail=50

# Test a camera stream directly from the pod
kubectl exec -n security deployment/shinobi -- \
  ffmpeg -rtsp_transport tcp \
  -i "rtsp://stream:<pass>@<ip>:554/cam/realmonitor?channel=1&subtype=0" \
  -t 5 -f null - 2>&1 | grep -E "Input|Video|error|401"

# MySQL root access
kubectl exec -n security deployment/shinobi -- \
  mysql -u root -p<MYSQL_ROOT_PASSWORD>

# List monitors with stream URLs
kubectl exec -n security deployment/shinobi -- \
  mysql -u shinobi -p<DB_PASSWORD> shinobi \
  -e "SELECT mid, name, JSON_UNQUOTE(JSON_EXTRACT(details, '$.auto_host')) FROM Monitors;"

# Check FFmpeg version in pod
kubectl exec -n security deployment/shinobi -- ffmpeg -version 2>&1 | head -1
```
