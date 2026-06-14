###############################################################################
# Eddie cloud cluster — provider + backend pinning
#
# Tooling: OpenTofu (drop-in Terraform replacement). All commands below work
# with either `tofu` or `terraform`, but the open-source `tofu` binary is the
# house default — fits the self-host/open ethos of the rest of this repo.
#
# State: kept LOCAL (terraform.tfstate in this dir, gitignored). It can contain
# secrets, so it is NEVER committed. Back it up to /whidbey/backups after every
# apply (see README). If you outgrow local state, switch to an S3-compatible
# backend (Hetzner Object Storage works) — but local is fine for one operator.
###############################################################################

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    hcloud = {
      source  = "hetznercloud/hcloud"
      version = "~> 1.49"
    }
  }
}

provider "hcloud" {
  token = var.hcloud_token
}
