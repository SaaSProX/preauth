#!/bin/bash
# Run this on the droplet after terraform provisioning
# Usage: ssh root@DROPLET_IP < infra/setup-droplet.sh

set -e

echo "==> Setting up PreAuth on droplet..."

cd /opt/preauth

# Create .env from template if not exists
if [ ! -f .env ]; then
  cat > .env << 'EOF'
# Anthropic
ANTHROPIC_API_KEY=

# HMO Database
HMO_DB_HOST=
HMO_DB_PORT=3306
HMO_DB_USER=
HMO_DB_PASSWORD=
HMO_DB_NAME=

# Webhook
WEBHOOK_SECRET=

# Gmail (for notifications)
GMAIL_CLIENT_ID=
GMAIL_CLIENT_SECRET=
GMAIL_REFRESH_TOKEN=
EOF
  echo "==> Created .env template - FILL IN VALUES"
fi

# Create docker-compose.yml
cat > docker-compose.yml << 'EOF'
services:
  preauth:
    image: ghcr.io/saasprox/preauth:latest
    container_name: preauth
    restart: unless-stopped
    ports:
      - "8000:8000"
    env_file:
      - .env
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  caddy:
    image: caddy:2-alpine
    container_name: caddy
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    depends_on:
      - preauth

volumes:
  caddy_data:
  caddy_config:
EOF

# Create Caddyfile (HTTP only - update with domain for HTTPS)
cat > Caddyfile << 'EOF'
:80 {
    reverse_proxy preauth:8000
}
EOF

echo "==> Setup complete!"
echo ""
echo "Next steps:"
echo "1. Edit .env with your credentials"
echo "2. Update Caddyfile with your domain for HTTPS"
echo "3. Run: docker compose up -d"
