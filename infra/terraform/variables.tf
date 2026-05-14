variable "do_token" {
  description = "DigitalOcean API token"
  type        = string
  sensitive   = true
}

variable "ssh_key_name" {
  description = "Name of SSH key in DigitalOcean account"
  type        = string
}

variable "droplet_name" {
  description = "Name for the droplet"
  type        = string
  default     = "preauth"
}

variable "region" {
  description = "DigitalOcean region"
  type        = string
  default     = "lon1" # London - close to Nigeria
}

variable "droplet_size" {
  description = "Droplet size slug"
  type        = string
  default     = "s-1vcpu-1gb" # $6/mo - 1 vCPU, 1GB RAM, 25GB SSD
}

variable "environment" {
  description = "Environment tag (dev, staging, prod)"
  type        = string
  default     = "prod"
}

variable "ssh_allowed_ips" {
  description = "IPs allowed to SSH (CIDR format)"
  type        = list(string)
  default     = ["0.0.0.0/0"] # Restrict this in production
}

# Optional: for DNS
# variable "domain" {
#   description = "Domain name managed in DO"
#   type        = string
# }
# 
# variable "subdomain" {
#   description = "Subdomain for the service"
#   type        = string
#   default     = "preauth"
# }
