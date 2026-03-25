# Cameras

All cameras are Amcrest, TCP port 37777, HTTP port 80, RTSP port 554.
Users: `admin` (full access), `automation` (admin group, for scripting), `stream` (viewer, RTSP only). Passwords in vault.

## Post-Firmware-Flash Setup

After flashing firmware + factory reset, run:

```bash
ansible-playbook configure_cameras.yml --limit <name>
```

**But first** — log into the web UI at `http://<ip>` as `admin`/`admin`. The camera forces a password change on first login. Set it to the vault value. The `modifyPassword` CGI does not work on this firmware; the web UI is the only way.

## Firmware CGI Quirks (V2.400.0002.15.R.20170810)

- `userManager.cgi` write ops (`addUser`, etc.) require `user.Reserved`, `user.Anonymous`, and `user.Sharable` fields or they return `Invalid Request`
- `modifyPassword` returns `Invalid Request` regardless — cannot change passwords via CGI
- `configManager.cgi?action=getConfig&name=MotionDetect` returns `Invalid Authority` for all users — firmware bug. `setConfig` works fine
- Motion detection param names: `Level` (not Sensitivity), `RecordEnable`, `SnapshotEnable`
- Image flip/mirror: `VideoInOptions[0].Flip=true` (180° rotation), `VideoInOptions[0].Mirror=true` (horizontal) — `VideoColor` namespace does NOT support these
- All CGI uses HTTP Digest auth (not Basic)

| Name | Model | IP |
|---|---|---|
| Backyard | IP4M-1025E | 10.0.1.157 |
| Front Porch | IP3M-956E | 10.0.1.163 |
| Driveway | IP4M-1025E | 10.0.1.164 |

## RTSP — Sub Stream (Homebridge / HomeKit)

```
-rtsp_transport tcp -i rtsp://stream:PASSWORD@10.0.1.157:554/cam/realmonitor?channel=1&subtype=1
-rtsp_transport tcp -i rtsp://stream:PASSWORD@10.0.1.163:554/cam/realmonitor?channel=1&subtype=1
-rtsp_transport tcp -i rtsp://stream:PASSWORD@10.0.1.164:554/cam/realmonitor?channel=1&subtype=1
```

## RTSP — Main Stream (Shinobi)

```
rtsp://stream:PASSWORD@10.0.1.157:554/cam/realmonitor?channel=1&subtype=0
rtsp://stream:PASSWORD@10.0.1.163:554/cam/realmonitor?channel=1&subtype=0
rtsp://stream:PASSWORD@10.0.1.164:554/cam/realmonitor?channel=1&subtype=0
```

## Shinobi FFmpeg Configuration

All monitors use `vcodec: copy` (stream passthrough). Do not change this to `none` or a named codec.

**Why**: Amcrest H.264 streams cause `libx264.so` segfaults when re-encoded. With `vcodec: copy`, ffmpeg remuxes the RTSP stream directly — no transcoding, no segfaults, lower CPU/memory usage.

Setting: Shinobi UI → Monitor Settings → Codec = "Copy". In the database: `JSON_EXTRACT(details, '$.vcodec')` should be `"copy"` for all monitors.

## Still Image

```
http://stream:PASSWORD@10.0.1.157:80/cgi-bin/snapshot.cgi
http://stream:PASSWORD@10.0.1.163:80/cgi-bin/snapshot.cgi
http://stream:PASSWORD@10.0.1.164:80/cgi-bin/snapshot.cgi
```
