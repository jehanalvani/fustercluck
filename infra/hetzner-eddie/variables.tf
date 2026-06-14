###############################################################################
# Input variables. Real values go in terraform.tfvars (gitignored).
# See terraform.tfvars.example for a template.
###############################################################################

variable "hcloud_token" {
  description = "Hetzner Cloud API token (Project → Security → API Tokens, Read & Write)."
  type        = string
  sensitive   = true
}

variable "server_name" {
  description = "Name of the Eddie node in the Hetzner console."
  type        = string
  default     = "eddie-01"
}

variable "location" {
  description = <<-EOT
    Hetzner location. hel1 = Helsinki, Finland (EU). Chosen for EU/German
    jurisdiction — data stored here falls under GDPR and is shielded from the US
    CLOUD Act (Hetzner Online GmbH has no US parent). Tradeoff vs the US options
    (hil = Hillsboro / ash = Ashburn): ~150 ms latency to a PNW home, which is
    imperceptible for trigger-based automation. EU locations also unlock the
    cheaper ARM (CAX) server types — see server_type.
  EOT
  type        = string
  default     = "hel1"
}

variable "server_type" {
  description = <<-EOT
    Hetzner server type. Default CX33 = 4 vCPU / 8 GB (x86, Intel, EU cost-optimized,
    ~EUR7/mo) — comfortably runs the full Eddie brain (n8n + LiteLLM + Qdrant +
    SearXNG). The cheaper ARM line (cax21) is ~half price BUT was sold out across
    ALL Hetzner locations when this was provisioned (Jun 2026); check current
    availability with ./check-availability.sh before trying it. Note: server-type
    availability is per-datacenter — fsn1 had no 4c/8g types free; hel1/nbg1 did.
    All Eddie images are multi-arch, so cax21 works whenever capacity returns.
  EOT
  type        = string
  default     = "cx33"
}

variable "image" {
  description = "Base OS image."
  type        = string
  default     = "ubuntu-24.04"
}

variable "ssh_public_key_path" {
  description = "Path to the SSH public key authorized for the root/ansible user on the node."
  type        = string
  default     = "~/.ssh/claude_homelab.pub"
}

variable "admin_cidrs" {
  description = <<-EOT
    Source CIDRs allowed to reach SSH (22) and the k3s API (6443). Lock this to
    your home/admin IP(s) — do NOT leave it open to the world. Example:
    ["203.0.113.4/32"]. 80/443 stay open to the world for Traefik/Let's Encrypt.
  EOT
  type        = list(string)
}

variable "data_volume_size" {
  description = <<-EOT
    Size (GB) of the attached Hetzner volume used for persistent app data
    (n8n DB + encryption-bound data, Qdrant). Keeping data on a separate volume
    means the node can be rebuilt without losing Eddie's brain. 0 = no volume
    (data lives on the node disk; lost on rebuild).
  EOT
  type        = number
  default     = 20
}

variable "labels" {
  description = "Labels applied to all Hetzner resources for grouping/billing."
  type        = map(string)
  default = {
    project = "eddie"
    managed = "opentofu"
  }
}
