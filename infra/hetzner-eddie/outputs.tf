###############################################################################
# Outputs. After `tofu apply`, feed eddie_ipv4 into the Ansible inventory
# (inventory_eddie.yml) and your DNS A record for the n8n hostname.
###############################################################################

output "eddie_ipv4" {
  description = "Public IPv4 of the Eddie node. Point your n8n DNS A record here, and set ansible_host to this in inventory_eddie.yml."
  value       = hcloud_server.eddie.ipv4_address
}

output "eddie_ipv6" {
  description = "Public IPv6 of the Eddie node."
  value       = hcloud_server.eddie.ipv6_address
}

output "eddie_status" {
  description = "Server power/provisioning status."
  value       = hcloud_server.eddie.status
}

output "data_volume_device" {
  description = "Linux device path of the attached data volume (empty if data_volume_size = 0)."
  value       = var.data_volume_size > 0 ? hcloud_volume.eddie_data[0].linux_device : ""
}
