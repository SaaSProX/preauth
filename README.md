# Aman HMO Pre-Authorization Agent

An AI-powered system that automates pre-authorization processing for Aman HMO. The agent evaluates requests against plan rules, patient eligibility, and utilization limits to deliver instant approve/deny/escalate decisions.

## Overview

```
┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐
│   Aman System   │ ───▶ │  Webhook API    │ ───▶ │  Claude Agent   │
│  (Pre-Auth Req) │      │  (FastAPI)      │      │  (Decision)     │
└─────────────────┘      └─────────────────┘      └────────┬────────┘
                                                           │
                    ┌──────────────────────────────────────┼──────────────────┐
                    │                                      │                  │
                    ▼                                      ▼                  ▼
           ┌───────────────┐                    ┌─────────────────┐   ┌──────────────┐
           │   Aman DB     │                    │  Dashboard DB   │   │    Email     │
           │   (MySQL)     │                    │  (PostgreSQL)   │   │ Notification │
           └───────────────┘                    └─────────────────┘   └──────────────┘
```

## How It Works

1. **Receive** — Webhook receives pre-auth request from Aman's system
2. **Fetch** — Agent retrieves patient data, plan details, and utilization from Aman DB
3. **Evaluate** — Agent applies plan rules from the knowledge base
4. **Decide** — Returns `approved`, `denied`, or `escalated` with reasoning
5. **Notify** — Sends email notification to the provider
6. **Log** — Records all steps for audit trail and dashboard

## Plan Tiers

| Plan | Annual Max | Pre-Auth Required |
|------|------------|-------------------|
| Bronze | ₦1,000,000 | Yes |
| Silver | ₦1,700,000 | Yes |
| Gold | ₦2,500,000 | Yes |
| Platinum | ₦3,500,000 | Yes |
| Platinum Plus | ₦5,000,000 | No (Express Card) |

## Decision Rules

**Auto-Approve:**
- Procedure is covered under patient's plan
- Amount is within remaining limit
- No waiting period or exclusion applies
- Platinum Plus members (no pre-auth required)

**Auto-Deny:**
- Procedure is globally excluded
- Exceeds plan limit or benefit cap
- Within waiting period (chronic: 6mo, pregnancy: 9mo, surgery: 12mo)
- Session/frequency limits exceeded (ICU days, CT/MRI scans, etc.)

**Escalate:**
- High-cost major surgery
- Remaining annual limit below 20%
- Ambiguous procedure mapping
- Multiple risk flags

## Project Structure

```
preauth/
├── main.py              # FastAPI application entry point
├── requirements.txt     # Python dependencies
├── migrate.sql          # Dashboard database schema
├── agent/
│   ├── agent.py         # Claude agent loop and tool execution
│   ├── prompts.py       # System prompt with knowledge base
│   └── tools.py         # Tool definitions for the agent
├── config/
│   └── settings.py      # Environment configuration
├── models/
│   └── schemas.py       # Pydantic models
├── services/
│   └── db.py            # Database connection utilities
└── webhook/
    └── router.py        # Webhook endpoint handler
```

## Setup

### Prerequisites

- Python 3.11+
- Access to Aman MySQL database
- PostgreSQL for dashboard logging
- Anthropic API key

### Installation

```bash
# Clone the repository
git clone https://github.com/SaaSProX/preauth.git
cd preauth

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Set up dashboard database
psql -d your_dashboard_db -f migrate.sql
```

### Configuration

Create a `.env` file:

```env
# Anthropic
ANTHROPIC_API_KEY=sk-ant-...

# Aman Database (MySQL)
AMAN_DB_HOST=
AMAN_DB_PORT=3306
AMAN_DB_USER=
AMAN_DB_PASSWORD=
AMAN_DB_NAME=

# Webhook Security
WEBHOOK_SECRET=your-shared-secret

# Email (Gmail)
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

### Webhook Endpoint

```
POST /webhook/preauth
```

**Request:**
```json
{
  "request_id": "REQ-2024-001234",
  "patient_id": "PAT-5678",
  "secret": "your-shared-secret"
}
```

**Response:**
```json
{
  "status": "received"
}
```

The request is acknowledged immediately and processed in the background.

### Health Check

```
GET /health
```

## License

Proprietary — Aman HMO / SaaSProX
