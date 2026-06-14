###############################################################################
# Eddie cloud node — Hetzner infrastructure
#
# Provisions exactly five resources (the whole point of using OpenTofu here is a
# clean `tofu destroy` so a teardown never orphans a billable volume/IP):
#   1. SSH key      — public key authorized on the node
#   2. Firewall     — 22/6443 locked to admin_cidrs; 80/443 open for Traefik
#   3. Volume       — persistent data disk for Eddie's brain (optional)
#   4. Server       — the k3s node itself (CPX31 in Hillsboro)
#   5. Volume attach — binds the volume to the server
#
# Everything ABOVE the OS (k3s, Helm, Eddie apps) is handled by Ansible — see
# deploy_eddie.yml. This file stops at "a reachable Ubuntu box with a data disk".
###############################################################################

resource "hcloud_ssh_key" "eddie" {
  name       = "${var.server_name}-key"
  public_key = file(pathexpand(var.ssh_public_key_path))
  labels     = var.labels
}

resource "hcloud_firewall" "eddie" {
  name   = "${var.server_name}-fw"
  labels = var.labels

  # SSH — admin only
  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "22"
    source_ips = var.admin_cidrs
  }

  # k3s API — admin only (kubectl/helm from your machine)
  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "6443"
    source_ips = var.admin_cidrs
  }

  # HTTP/HTTPS — world (Traefik ingress + Let's Encrypt HTTP-01 challenge)
  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "80"
    source_ips = ["0.0.0.0/0", "::/0"]
  }
  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "443"
    source_ips = ["0.0.0.0/0", "::/0"]
  }
}

resource "hcloud_volume" "eddie_data" {
  count    = var.data_volume_size > 0 ? 1 : 0
  name     = "${var.server_name}-data"
  size     = var.data_volume_size
  location = var.location
  format   = "ext4"
  labels   = var.labels
}

resource "hcloud_server" "eddie" {
  name         = var.server_name
  server_type  = var.server_type
  image        = var.image
  location     = var.location
  ssh_keys     = [hcloud_ssh_key.eddie.id]
  firewall_ids = [hcloud_firewall.eddie.id]
  labels       = var.labels

  public_net {
    ipv4_enabled = true
    ipv6_enabled = true
  }

  # cloud-init: create the `ansible` user (matches the rest of the repo's remote_user),
  # authorize the same key, and format/mount the data volume at /mnt/eddie-data so
  # k3s local-path storage can live on the durable disk. Keeps Ansible's first
  # connection clean (no root login needed after this).
  user_data = templatefile("${path.module}/cloud-init.yaml.tftpl", {
    ssh_public_key = file(pathexpand(var.ssh_public_key_path))
    has_volume     = var.data_volume_size > 0
  })
}

resource "hcloud_volume_attachment" "eddie_data" {
  count     = var.data_volume_size > 0 ? 1 : 0
  volume_id = hcloud_volume.eddie_data[0].id
  server_id = hcloud_server.eddie.id
  automount = false # handled deterministically in cloud-init by volume id
}
