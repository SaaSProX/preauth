terraform {
  required_version = ">= 1.0"

  required_providers {
    digitalocean = {
      source  = "digitalocean/digitalocean"
      version = "~> 2.0"
    }
  }
}

provider "digitalocean" {
  token = var.do_token
}

# SSH key (must exist in your DO account)
data "digitalocean_ssh_key" "main" {
  name = var.ssh_key_name
}

# Firewall
resource "digitalocean_firewall" "preauth" {
  name = "preauth-firewall"

  droplet_ids = [digitalocean_droplet.preauth.id]

  # SSH
  inbound_rule {
    protocol         = "tcp"
    port_range       = "22"
    source_addresses = var.ssh_allowed_ips
  }

  # HTTP (redirect to HTTPS)
  inbound_rule {
    protocol         = "tcp"
    port_range       = "80"
    source_addresses = ["0.0.0.0/0", "::/0"]
  }

  # HTTPS
  inbound_rule {
    protocol         = "tcp"
    port_range       = "443"
    source_addresses = ["0.0.0.0/0", "::/0"]
  }

  # All outbound
  outbound_rule {
    protocol              = "tcp"
    port_range            = "1-65535"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }

  outbound_rule {
    protocol              = "udp"
    port_range            = "1-65535"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }

  outbound_rule {
    protocol              = "icmp"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }
}

# Droplet
resource "digitalocean_droplet" "preauth" {
  name     = var.droplet_name
  region   = var.region
  size     = var.droplet_size
  image    = "ubuntu-22-04-x64"
  ssh_keys = [data.digitalocean_ssh_key.main.id]

  user_data = <<-EOF
    #!/bin/bash
    set -e

    # Update system
    apt-get update
    apt-get upgrade -y

    # Install Docker
    curl -fsSL https://get.docker.com | sh

    # Install Docker Compose
    apt-get install -y docker-compose-plugin

    # Create app directory
    mkdir -p /opt/preauth
    chown -R root:root /opt/preauth

    # Enable Docker
    systemctl enable docker
    systemctl start docker
  EOF

  tags = ["preauth", var.environment]
}

# Optional: DNS record (if using DigitalOcean DNS)
# resource "digitalocean_record" "preauth" {
#   domain = var.domain
#   type   = "A"
#   name   = var.subdomain
#   value  = digitalocean_droplet.preauth.ipv4_address
#   ttl    = 300
# }
