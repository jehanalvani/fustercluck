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
  description = "Hetzner location. hil = Hillsboro, OR (US-West, closest to home)."
  type        = string
  default     = "hil"
}

variable "server_type" {
  description = <<-EOT
    Hetzner server type. CPX31 = 4 vCPU / 8 GB, the smallest comfortable size for
    the full Eddie brain (n8n + LiteLLM + Qdrant + SearXNG). Note: the cheap CAX
    (ARM) and CX lines are EU-only — US locations only get CPX/CCX.
  EOT
  type        = string
  default     = "cpx31"
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
