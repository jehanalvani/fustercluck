# Pi-hole

Split-horizon DNS, ad-blocking, and DHCP for the home network. Runs as a Docker container on **kube01** with its own LAN IP via macvlan — no NAT or port-forwarding needed.

## Access

- **Admin**: http://10.0.1.250/admin
- **DNS IP**: 10.0.1.250
- **DHCP**: Pi-hole is the DHCP server for the LAN (Orbi DHCP is disabled)

## Why Not k8s?

Pi-hole intentionally runs outside the k3s cluster as a standalone Docker container. DNS is foundational — if it goes down, nothing on the LAN resolves, which makes debugging cluster problems much harder. Running inside k8s introduces unnecessary failure modes (pod scheduling, network policy, cluster health) for a service that predates the cluster and works reliably as-is.

Exposing port 53 from k8s is also awkward: CoreDNS already occupies port 53 cluster-internally, so pihole would need either `hostNetwork: true` (pinning it to one node) or a LoadBalancer service with a dedicated LAN IP — adding a dependency on klipper-lb just to get a stable DNS address. The macvlan approach gives a cleaner, more stable result with less complexity.

## Why kube01, not 20-size?

Pi-hole was previously on 20-size but was moved because 20-size is the most loaded machine in the cluster — NFS server, GPU, Plex, Shinobi. NFS lock events caused host-level resource starvation that made Pi-hole's DNS daemon unresponsive (even though the web UI still responded), taking down DNS for the entire LAN.

kube01 runs only the k3s control plane and Traefik. It's the lightest-loaded node and is already the most critical to keep alive (Traefik ingress). Co-locating DNS with the control plane aligns failure domains: if kube01 is down, the cluster is down anyway.

## DNS Fallback

DHCP leases hand out both `10.0.1.250` (Pi-hole) and `10.0.1.1` (router) as DNS servers via `dhcp-option=6`. Clients automatically fall back to the router's DNS if Pi-hole is unresponsive, preventing a full LAN outage. Configured in `roles/pihole/tasks/main.yml` via `03-dhcp-dns-fallback.conf` in the dnsmasq config directory.

## Architecture

Pi-hole uses a Docker macvlan network (`lan`) on kube01's physical LAN interface (`eth0`), giving the container a real LAN IP (10.0.1.250) that is directly reachable by all LAN clients.

Pi-hole is the DHCP server for the LAN — clients receive `10.0.1.250` directly as their DNS server, so Pi-hole sees real client IPs in query logs (not the router IP).

## Local DNS (Split-Horizon)

`pihole_ingress_ip` in `roles/pihole/vars/main.yml` controls the IP all `*.alvani.me` services resolve to on the LAN. This must point to a node where **klipper-lb (svclb) is running** — currently `10.0.1.30` (kube01).

> **Do not set `pihole_ingress_ip` to 20-size (10.0.1.203).** The klipper-lb DaemonSet does not tolerate the `dedicated=media:NoSchedule` taint, so it never runs on 20-size. Setting the DNS to 20-size means nothing is listening on port 443 there and all `*.alvani.me` services become unreachable from the LAN.

klipper-lb runs on kube01, kube02, kube03 — any of these IPs work. kube01 (10.0.1.30) is preferred since the Traefik pod itself runs there.

### DNS ownership split

- **`*.alvani.me` cluster services** — seeded from `pihole_local_hosts` in `vars/main.yml` on fresh deploys only (`pihole --config dns.hosts`). After initial deploy these are editable in the Pi-hole UI (Local DNS > DNS Records) and Ansible will not overwrite them. To force a re-seed, recreate the container and re-run the role.
- **All other local records** (e.g. `printer.home`, `nas.local`) — add freely via the Pi-hole UI. These are stored in Pi-hole's database and are never touched by Ansible.

## DHCP

Pi-hole handles DHCP for the entire `10.0.1.0/24` subnet (pool: `10.0.1.2–10.0.1.249`). Static leases for all known devices are managed in `pihole_dhcp_hosts` in `vars/main.yml` and passed to the container via `FTLCONF_dhcp_hosts`. They are visible and editable in the Pi-hole UI under **Settings > DHCP > Static DHCP configuration**.

To add a new static lease, add an entry to `pihole_dhcp_hosts` and re-run the Ansible role — do not add leases manually in the UI as they will be overwritten on next deploy.

## Configuration

Managed by the `pihole` Ansible role:

```bash
ansible-playbook 20-size_config.yml --tags pihole
```

Key config in `roles/pihole/vars/main.yml`:
- `pihole_local_hosts` — cluster service DNS entries (`{ ip, hostname }`); all currently point to `10.0.1.30` (klipper-lb on kube01)
- `pihole_dhcp_hosts` — static DHCP leases (`{ mac, ip, name }`)
- `pihole_upstream_dns` — upstream resolvers (Unlocator SmartDNS + Cloudflare)
- `pihole_web_password` — inline vault-encrypted; decrypted via `passfile.txt` → 1Password

## Adding a New Cluster Service

Add an entry to `pihole_local_hosts` in `vars/main.yml` and recreate the Pi-hole container so the seed task runs:

```bash
# On 20-size:
docker stop pihole && docker rm pihole
ansible-playbook 20-size_config.yml --tags pihole
```

Alternatively, add the DNS record directly in the Pi-hole UI (Local DNS > DNS Records) — it will persist and never be overwritten by Ansible.

## Upstream DNS

`185.37.37.37` (Unlocator SmartDNS for geo-unblocking) with `1.1.1.1` (Cloudflare) as fallback. Editable in the Pi-hole UI under **Settings > DNS**. Seeded from `pihole_upstream_dns` in `vars/main.yml` on fresh deploys only.
