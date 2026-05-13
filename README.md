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
├── main.py              # FastAPI application entry point
├── requirements.txt     # Python dependencies
├── migrate.sql          # Dashboard database schema
├── kb/                  # Knowledge base configs (per HMO)
│   └── {hmo}_plans.json
├── agent/
│   ├── agent.py         # AI agent loop and tool execution
│   ├── prompts.py       # System prompt builder
│   └── tools.py         # Tool definitions
├── config/
│   └── settings.py      # Environment configuration
├── models/
│   └── schemas.py       # Pydantic models
├── services/
│   └── db.py            # Database utilities
└── webhook/
    └── router.py        # Webhook endpoint handler
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

### Running

```bash
# Development
uvicorn main:app --reload --port 8000

# Production
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

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
