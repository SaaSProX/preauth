# PreAuth

AI-powered pre-authorization automation for Health Maintenance Organizations (HMOs) in Nigeria.

## Overview

PreAuth is an intelligent agent that automates the pre-authorization workflow for health insurance claims. It evaluates requests against plan rules, patient eligibility, and utilization limits to deliver instant approve/deny/escalate decisions — reducing processing time from hours to seconds.

```
┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐
│   HMO System    │ ───▶ │  Webhook API    │ ───▶ │   AI Agent      │
│  (Pre-Auth Req) │      │  (FastAPI)      │      │  (Decision)     │
└─────────────────┘      └─────────────────┘      └────────┬────────┘
                                                           │
                    ┌──────────────────────────────────────┼──────────────────┐
                    │                                      │                  │
                    ▼                                      ▼                  ▼
           ┌───────────────┐                    ┌─────────────────┐   ┌──────────────┐
           │    HMO DB     │                    │  Dashboard DB   │   │ Notification │
           │   (MySQL)     │                    │  (PostgreSQL)   │   │   Service    │
           └───────────────┘                    └─────────────────┘   └──────────────┘
```

## How It Works

1. **Receive** — Webhook receives pre-auth request from HMO's existing system
2. **Fetch** — Agent retrieves patient data, plan details, and utilization history
3. **Evaluate** — Agent applies plan-specific rules from the knowledge base
4. **Decide** — Returns `approved`, `denied`, or `escalated` with clear reasoning
5. **Notify** — Sends notification to the healthcare provider
6. **Log** — Records all steps for audit trail and analytics

## Features

- **Instant Decisions** — Sub-minute processing vs hours of manual review
- **Configurable Rules** — Each HMO defines their own plans, limits, and exclusions
- **Full Audit Trail** — Every decision logged with reasoning for compliance
- **Escalation Handling** — Edge cases flagged for human review
- **Multi-HMO Support** — Single deployment, multiple HMO configurations

## Decision Logic

The agent evaluates each request against the HMO's knowledge base:

**Auto-Approve when:**
- Procedure is covered under patient's plan
- Amount is within remaining benefit limit
- No waiting period or exclusion applies
- Plan type allows automatic approval

**Auto-Deny when:**
- Procedure is globally excluded
- Benefit limit exceeded
- Within waiting period for the service category
- Session/frequency limits exceeded

**Escalate when:**
- High-cost procedures requiring review
- Patient near annual limit threshold
- Ambiguous procedure mapping
- Multiple risk indicators present

## Project Structure

```
preauth/
├── main.py                # FastAPI application entry point
├── Dockerfile             # Container image
├── docker-compose.yml     # Local development
├── requirements.txt       # Python dependencies
├── migrate.sql            # Dashboard database schema
├── kb/                    # Knowledge base configs (per HMO)
│   └── {hmo}_plans.json
├── agent/
│   ├── agent.py           # AI agent loop and tool execution
│   ├── prompts.py         # System prompt builder
│   └── tools.py           # Tool definitions
├── config/
│   └── settings.py        # Environment configuration
├── models/
│   └── schemas.py         # Pydantic models
├── services/
│   └── db.py              # Database utilities
├── webhook/
│   └── router.py          # Webhook endpoint handler
├── infra/
│   ├── terraform/         # Infrastructure as Code
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   └── outputs.tf
│   └── setup-droplet.sh   # Server setup script
└── .github/
    └── workflows/
        └── deploy.yml     # CI/CD pipeline
```

## Setup

### Prerequisites

- Python 3.11+
- PostgreSQL (dashboard/logging)
- Access to HMO's patient database
- Anthropic API key

### Installation

```bash
git clone https://github.com/SaaSProX/preauth.git
cd preauth

python -m venv venv
source venv/bin/activate

pip install -r requirements.txt

# Initialize dashboard database
psql -d preauth_dashboard -f migrate.sql
```

### Configuration

Create a `.env` file:

```env
# AI Provider
ANTHROPIC_API_KEY=sk-ant-...

# HMO Database (MySQL)
HMO_DB_HOST=
HMO_DB_PORT=3306
HMO_DB_USER=
HMO_DB_PASSWORD=
HMO_DB_NAME=

# Webhook Security
WEBHOOK_SECRET=

# Notifications (Gmail)
GMAIL_CLIENT_ID=
GMAIL_CLIENT_SECRET=
GMAIL_REFRESH_TOKEN=
```

### Running Locally

```bash
# Development
uvicorn main:app --reload --port 8000

# With Docker
docker compose up --build
```

## Deployment

PreAuth deploys to a DigitalOcean Droplet using Terraform for infrastructure and GitHub Actions for CI/CD.

### Infrastructure

**Droplet Specs:**
- Size: `s-1vcpu-1gb` ($6/month)
- Region: `lon1` (London)
- OS: Ubuntu 22.04 + Docker
- Reverse Proxy: Caddy (automatic HTTPS)

### 1. Provision Infrastructure

```bash
cd infra/terraform

# Configure
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your values:
#   - do_token (DigitalOcean API token)
#   - ssh_key_name (name of SSH key in your DO account)

# Deploy
terraform init
terraform apply
```

Terraform outputs the droplet IP address.

### 2. Setup Server

```bash
# Run setup script on the new droplet
ssh root@<DROPLET_IP> 'bash -s' < infra/setup-droplet.sh

# SSH in and configure environment
ssh root@<DROPLET_IP>
cd /opt/preauth
nano .env  # Add your credentials
```

### 3. Configure GitHub Actions

Add these secrets to your GitHub repository (Settings → Secrets → Actions):

| Secret | Description |
|--------|-------------|
| `DO_HOST` | Droplet IP address |
| `DO_SSH_KEY` | Private SSH key for deployment |

### 4. Deploy

Push to `main` branch triggers automatic deployment:

```
Push → Build Docker Image → Push to GHCR → SSH Deploy → Restart Container
```

Or trigger manually: Actions → Deploy → Run workflow

### Manual Deployment

If needed, deploy manually on the server:

```bash
ssh root@<DROPLET_IP>
cd /opt/preauth
docker compose pull
docker compose up -d
```

### Adding HTTPS

Edit `/opt/preauth/Caddyfile` on the server:

```
preauth.yourdomain.com {
    reverse_proxy preauth:8000
}
```

Then restart Caddy:

```bash
docker compose restart caddy
```

Caddy automatically provisions SSL certificates via Let's Encrypt.

## API

### Submit Pre-Auth Request

```
POST /webhook/preauth
```

```json
{
  "request_id": "REQ-2024-001234",
  "patient_id": "PAT-5678",
  "secret": "webhook-secret"
}
```

Response (immediate):
```json
{
  "status": "received"
}
```

Processing happens asynchronously. Results are written to the HMO database and notifications sent to providers.

### Health Check

```
GET /health
```

## Adding a New HMO

1. Create knowledge base file: `kb/{hmo_name}_plans.json`
2. Define plans, limits, exclusions, and decision rules
3. Configure database connection for the HMO
4. Set up webhook integration with HMO's system

See `kb/example_plans.json` for the schema structure.

## Current Pilot

**Aman HMO** — First integration partner for pilot testing.

## License

Proprietary — Saaspro Labs
