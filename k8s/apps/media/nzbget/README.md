# NZBGet

Usenet download client. Config is managed as a file (not env vars) due to complexity.

## Paths

| Container path | NFS path (20-size.local) | Purpose |
|---|---|---|
| `/media` | `/snoqualmie/media` | Downloads destination |
| `/intermediate` | `/whidbey/configs/nzbget/intermediate` | In-progress downloads |
| `/config` | nfs-client PVC (auto) | App config & state |

NZBGet internal paths (relative to `MainDir=/media/nzb_downloads`):

| Setting | Path |
|---|---|
| DestDir | `/media/nzb_downloads/completed` |
| NzbDir | `/media/nzb_downloads/nzb` |
| QueueDir | `/media/nzb_downloads/queue` |
| TempDir | `/media/nzb_downloads/tmp` |
| InterDir | `/intermediate` (separate NFS mount) |

## News Servers

| # | Name | Host | Port | SSL | Level | Role |
|---|---|---|---|---|---|---|
| 1 | NewsDemon | news.newsdemon.com | 563 | yes | 0 | Primary |
| 2 | SuperNews | news.supernews.com | 119 | no | 0 | Primary |
| 3 | ViperNews | news.vipernews.com | 563 | yes | 1 | Fill |

## Credentials

Managed via Ansible Vault in `roles/nzbget/vars/main.yml`. Control username is `nzbget`.

To view/update: `ansible-vault view roles/nzbget/vars/main.yml --vault-id fustercluck@prompt`

> **Note:** The `nzbget-secret` k8s Secret in the `media` namespace is not referenced by the
> pod and contains stale credentials. It is not used — config is deployed directly to the
> `/config` PVC via `kubectl cp`.

## Deploying / Updating Config

The Ansible role (`roles/nzbget`) renders `nzbget.conf.j2` with vault-decrypted values.
For manual updates, render the config and copy it in:

```bash
# Copy updated config to pod
kubectl cp /tmp/nzbget.conf media/<pod-name>:/config/nzbget.conf

# Restart to apply
kubectl rollout restart deployment/nzbget -n media

# Destroy plaintext immediately after
rm -P /tmp/nzbget.conf
```

## Notable Config

| Setting | Value | Reason |
|---|---|---|
| ArticleCache | 500 MB | Reduces disk fragmentation |
| WriteBuffer | 1024 KB | Efficient NFS writes |
| ParBuffer | 250 MB | Fast par2 verification |
| WriteLog | rotate (3 files) | Prevents unbounded log growth |
| FormAuth | yes | Form-based login (not browser basic auth) |
| OutputMode | loggable | No TTY in container |
| ContinuePartial | no | Fast local NFS connection |
