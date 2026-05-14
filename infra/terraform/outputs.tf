output "droplet_ip" {
  description = "Public IP address of the droplet"
  value       = digitalocean_droplet.preauth.ipv4_address
}

output "droplet_id" {
  description = "Droplet ID"
  value       = digitalocean_droplet.preauth.id
}

output "droplet_urn" {
  description = "Droplet URN for referencing"
  value       = digitalocean_droplet.preauth.urn
}

output "ssh_command" {
  description = "SSH command to connect"
  value       = "ssh root@${digitalocean_droplet.preauth.ipv4_address}"
}

output "webhook_url" {
  description = "Webhook endpoint URL (update with your domain)"
  value       = "http://${digitalocean_droplet.preauth.ipv4_address}/webhook/preauth"
}
