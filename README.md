# PreAuth (backend)

AI-powered pre-authorization automation for Health Maintenance Organizations (HMOs) in Nigeria.

The companion **operations dashboard** lives in its own repo:
[`SaaSProX/preauth-intake-dashboard`](https://github.com/SaaSProX/preauth-intake-dashboard).

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
├── main.py                # FastAPI app entry point + CORS config
├── Dockerfile             # Container image
├── docker-compose.yml     # Local development
├── requirements.txt       # Python deps (FastAPI, asyncpg, anthropic, httpx, …)
├── migrate.sql            # Postgres schema — run on prod DB before deploy
├── agent/
│   └── agent.py           # 4-agent pipeline: Eligibility → Coverage → Limits → Decision
├── auth/
│   ├── router.py          # All /auth/* endpoints (dashboard, login, onboarding, team, api keys, patient-history, preauth-events)
│   └── utils.py           # JWT, password hashing, session helpers
├── config/
│   └── settings.py        # Environment configuration
├── services/
│   ├── db.py              # Postgres helpers (pg_query_one / pg_query_all / pg_execute)
│   ├── aman_callback.py   # Posts agent decisions back to Aman
│   ├── preauth_events.py  # Tracks each intake-webhook delivery per check-in
│   ├── webhook_delivery.py # Logs every inbound webhook attempt (auth, parse, insert)
│   ├── invites.py         # Invite link tokens
│   └── notifier.py        # Resend transactional email
├── webhook/
│   └── router.py          # /webhook/preauth — authenticated intake from HMO
├── infra/                 # Terraform + droplet bootstrap
└── .github/workflows/     # CI/CD
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

# Our Postgres (Supabase in prod, Homebrew local in dev)
OUR_DB_URL=postgresql://user:pass@host:5432/dbname

# Auth — sessions are 7-day JWTs (HS256)
JWT_SECRET=<long random string>

# Dashboard origin allowlist for CORS (comma-separated; defaults to local dev)
CORS_ORIGINS=https://dashboard.yourdomain.com,https://staging.yourdomain.com

# Outbound: send agent decisions back to Aman
AMAN_DECISIONS_URL=https://aman.example/api/preauth/decisions
KPA_KEY=<bearer key Aman gave you>

# Dashboard
DASHBOARD_BASE_URL=https://dashboard.yourdomain.com

# Notifications (Resend)
RESEND_API_KEY=
RESEND_FROM_EMAIL="Saaspro Lab <no-reply@saasprolabs.io>"
```

`CORS_ORIGINS`, `AMAN_DECISIONS_URL`, and `KPA_KEY` are recent additions. The backend will start without them but: missing `CORS_ORIGINS` blocks the dashboard from calling the API, and missing `AMAN_DECISIONS_URL`/`KPA_KEY` makes the agent skip the decision callback (logged as `skipped_no_config`).

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

## Multi-tenancy

Every table that holds PA data has an `org_id` column. Every dashboard query ends with `WHERE org_id = $1` where `$1` comes from the caller's JWT (not from any URL param). One backend, many HMO tenants, strict row-level isolation.

**Two roles, one URL, one dashboard.** The DB stores exactly two roles on the `clients` table: `admin` and `member`. There is no separate "super-admin" tier — what we call a "platform admin" is just **an admin whose org happens to be SaaSPro**. Same login form, same dashboard URL; the sidebar adapts based on which org the JWT belongs to.

| Account | role | org | What they see |
|---|---|---|---|
| SaaSPro platform admin | `admin` | `SAASPRO` | Own org by default + Onboarding nav + can drill into any client org via `?org_id=` (read-only) |
| HMO admin (e.g. Aman ops lead) | `admin` | `AMAN` | Only `AMAN` — full read + write on team, API keys, queue. No Onboarding. |
| HMO member (e.g. Aman analyst) | `member` | `AMAN` | Only `AMAN`, read-only. Write actions hidden by CSS. |

The dashboard's `?org_id=` drill-in is **server-side checked** (`is_platform_admin(claims)` in `auth/router.py`) — passing it as a non-platform-admin returns 403. The check is literally `role == 'admin' AND org_name == 'SAASPRO'`.

## API endpoints

All `/auth/*` endpoints require a `Bearer` JWT in the `Authorization` header. `/webhook/*` uses an API key on the request body.

### Webhook (HMO → us)

```
POST /webhook/preauth
```
Authenticated by an `api_key` field (issued per org from the dashboard's API Keys page). Receives → acks fast → kicks off the 4-agent pipeline in the background. Every delivery (including bad keys, malformed payloads) is logged to `webhook_delivery_logs`. Every successful intake is also logged to `preauth_events` so the dashboard can show the full intake history per check-in (first capture + later additions).

### Dashboard endpoints (dashboard → us)

```
POST /auth/login                          # email + password → JWT
GET  /auth/preauth-dashboard              # paginated queue + summary + chart series
GET  /auth/patient-history?patient_id=    # every PA for one patient in caller's org
GET  /auth/preauth-events?checkin_id=     # full delivery timeline for one PA
GET  /auth/webhook-delivery-logs          # integration health view (failed/passed webhooks)
GET  /auth/webhook-audit-trail            # per-request pipeline replay
GET  /auth/team           POST /auth/team/invite                DELETE /auth/team/{email}
GET  /auth/api-keys       POST /auth/api-keys/generate          DELETE /auth/api-keys/{id}
GET  /auth/onboarding/orgs                POST /auth/onboarding/orgs           (platform admin only)
PATCH /auth/onboarding/orgs/{id}                                                 (platform admin only)
```

`/auth/preauth-dashboard` supports:
- `page` (1+), `page_size` (1–200, default 25)
- `date_from`, `date_to` (YYYY-MM-DD)
- `plan=Bronze|Silver|Gold|…` — case-insensitive ILIKE match
- `q=…` — searches `patient_id`, `request_id`, `decision`, and the full `raw_payload::text`
- `org_id=N` — platform-admin drill-in

Response includes per-row `patient_pa_count`, `event_count`, plus `meta.data_window` (earliest/latest received_at) and `meta.plans` (deduped distinct plans for the dropdown).

### Health Check

```
GET /health
```

## Adding a New HMO

1. **Create the org** from the dashboard's Onboarding view (platform admin only).
2. **Invite the HMO's first admin** by email — they get a link to set their password.
3. **Generate an API key** for them from the dashboard's API Keys page.
4. They point their webhook at `POST /webhook/preauth` with that key.
5. Their PAs land tagged with their `org_id` automatically and never leak into another tenant.

**Caveat:** the AI agent's plan benefits + exclusions knowledge base is currently hard-coded for Aman in `agent/agent.py`. Until that's parameterized per org (planned), a new HMO's PAs will be evaluated against Aman's rules. The dashboard isolation works fully; the AI accuracy is what's tenant-coupled.

## Schema migrations

`migrate.sql` is **manual** — there is no auto-migrate on startup. Before deploying a release that introduces new columns or tables, run it against the target Postgres:

```bash
psql "$OUR_DB_URL" -f migrate.sql
```

Every statement uses `IF NOT EXISTS`, so the file is safe to re-run.

The one statement to watch is the `received_at TIMESTAMPTZ` conversion — it interprets existing naive timestamps as UTC. If your prod has been inserting in another tz, sanity-check the existing rows first.

## Current Pilot

**Aman HMO** — first integration partner. PAs flow in from Aman's `pa.submitted` / `pa.items_added` webhooks; agent decisions flow back via `AMAN_DECISIONS_URL` (advisory mode while mapping is being verified).

## License

Proprietary — Saaspro Labs
