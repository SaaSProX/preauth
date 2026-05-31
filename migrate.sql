-- Our PostgreSQL schema
-- Run once to set up the dashboard DB

-- Organizations (Aman HMO and future clients)
CREATE TABLE IF NOT EXISTS organizations (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(100) NOT NULL,
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP DEFAULT NOW()
);

-- Clients (users — admin or member)
CREATE TABLE IF NOT EXISTS clients (
    id              SERIAL PRIMARY KEY,
    org_id          INT REFERENCES organizations(id),
    name            VARCHAR(100) NOT NULL,
    email           VARCHAR(100) NOT NULL UNIQUE,
    password_hash   VARCHAR(255) NOT NULL,
    role            VARCHAR(20) DEFAULT 'member',  -- admin | member
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP DEFAULT NOW()
);

-- Invites (you invite admin, admin invites members)
CREATE TABLE IF NOT EXISTS invites (
    id              SERIAL PRIMARY KEY,
    org_id          INT REFERENCES organizations(id),
    email           VARCHAR(100) NOT NULL UNIQUE,
    token           VARCHAR(100) NOT NULL UNIQUE,
    role            VARCHAR(20) DEFAULT 'member',       -- admin | member
    invited_by      INT REFERENCES clients(id),         -- NULL if you invited the admin
    used            BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMP DEFAULT NOW()
);

-- API keys (one per org)
CREATE TABLE IF NOT EXISTS api_clients (
    id              SERIAL PRIMARY KEY,
    org_id          INT REFERENCES organizations(id),
    user_id         INT REFERENCES clients(id),
    client_name     VARCHAR(100) NOT NULL,
    api_key         VARCHAR(100) NOT NULL UNIQUE,
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP DEFAULT NOW()
);

ALTER TABLE api_clients ADD COLUMN IF NOT EXISTS user_id INT REFERENCES clients(id);
ALTER TABLE api_clients ADD COLUMN IF NOT EXISTS last_used_at TIMESTAMPTZ;

-- Every incoming webhook request
CREATE TABLE IF NOT EXISTS preauth_logs (
    id              SERIAL PRIMARY KEY,
    org_id          INT REFERENCES organizations(id),
    request_id      VARCHAR(100) NOT NULL UNIQUE,
    patient_id      VARCHAR(100) NOT NULL,
    raw_payload     JSONB,
    extracted_fields JSONB,
    received_at     TIMESTAMP DEFAULT NOW(),
    status          VARCHAR(20) DEFAULT 'pending'   -- pending | processing | completed | failed
);

ALTER TABLE preauth_logs ADD COLUMN IF NOT EXISTS raw_payload JSONB;
ALTER TABLE preauth_logs ADD COLUMN IF NOT EXISTS extracted_fields JSONB;
ALTER TABLE preauth_logs ADD COLUMN IF NOT EXISTS agent_step TEXT;
ALTER TABLE preauth_logs ADD COLUMN IF NOT EXISTS decision TEXT;
ALTER TABLE preauth_logs ADD COLUMN IF NOT EXISTS agent_result JSONB;
ALTER TABLE preauth_logs ADD COLUMN IF NOT EXISTS error_message TEXT;
ALTER TABLE preauth_logs ADD COLUMN IF NOT EXISTS processed_at TIMESTAMPTZ;
ALTER TABLE preauth_logs ADD COLUMN IF NOT EXISTS callback_status TEXT;
ALTER TABLE preauth_logs ADD COLUMN IF NOT EXISTS callback_http_status INT;
ALTER TABLE preauth_logs ADD COLUMN IF NOT EXISTS callback_sent_at TIMESTAMPTZ;
ALTER TABLE preauth_logs ADD COLUMN IF NOT EXISTS callback_error TEXT;
-- Standardize received_at to a timezone-aware type so latency math is honest
-- (matches processed_at and agent_logs.logged_at, both TIMESTAMPTZ). The
-- AT TIME ZONE clause interprets existing naive values as UTC; operators
-- should adjust to their server TZ if rows were written in another zone.
ALTER TABLE preauth_logs ALTER COLUMN received_at TYPE TIMESTAMPTZ USING received_at AT TIME ZONE 'UTC';

-- Every AMAN PA business event payload, preserving full per-event history
CREATE TABLE IF NOT EXISTS preauth_events (
    id                    SERIAL PRIMARY KEY,
    org_id                INT REFERENCES organizations(id),
    preauth_log_id        INT REFERENCES preauth_logs(id),
    event_id              VARCHAR(100) NOT NULL UNIQUE,
    event_type            VARCHAR(100),
    correlation_id        VARCHAR(100),
    checkin_id            VARCHAR(100) NOT NULL,
    request_id            VARCHAR(100) NOT NULL,
    patient_id            VARCHAR(100),
    facility_name         TEXT,
    insurance_no          VARCHAR(100),
    policy_no             VARCHAR(100),
    plan_name             VARCHAR(100),
    event_sequence        INT NOT NULL DEFAULT 1,
    occurred_at           TIMESTAMPTZ,
    submitted_at          TIMESTAMPTZ,
    item_count            INT,
    total_requested_cost  NUMERIC,
    items_added_count     INT,
    items_added_total     NUMERIC,
    raw_payload           JSONB NOT NULL,
    extracted_fields      JSONB,
    payload_summary       JSONB,
    duplicate_count       INT NOT NULL DEFAULT 0,
    first_seen_at         TIMESTAMPTZ DEFAULT NOW(),
    last_seen_at          TIMESTAMPTZ DEFAULT NOW(),
    created_at            TIMESTAMPTZ DEFAULT NOW(),
    updated_at            TIMESTAMPTZ DEFAULT NOW()
);

-- Every inbound webhook delivery attempt, including failures before PA persistence
CREATE TABLE IF NOT EXISTS webhook_delivery_logs (
    id                    SERIAL PRIMARY KEY,
    delivery_id           UUID NOT NULL UNIQUE,
    provider              VARCHAR(50) NOT NULL DEFAULT 'aman',
    org_id                INT REFERENCES organizations(id),
    api_client_id          INT REFERENCES api_clients(id),
    api_key_hint           VARCHAR(32),
    request_method        VARCHAR(10),
    request_path          TEXT,
    request_ip            VARCHAR(100),
    user_agent            TEXT,
    event_id              VARCHAR(100),
    event_type            VARCHAR(100),
    correlation_id        VARCHAR(100),
    checkin_id            VARCHAR(100),
    facility_name         TEXT,
    insurance_no          VARCHAR(100),
    policy_no             VARCHAR(100),
    plan_name             VARCHAR(100),
    auth_status           VARCHAR(40) NOT NULL DEFAULT 'not_checked',
    payload_received      BOOLEAN NOT NULL DEFAULT FALSE,
    payload_valid         BOOLEAN NOT NULL DEFAULT FALSE,
    payload_status        VARCHAR(40) NOT NULL DEFAULT 'not_read',
    payload_size_bytes    INT,
    payload_summary       JSONB,
    db_insert_status      VARCHAR(40) NOT NULL DEFAULT 'not_attempted',
    preauth_request_id    VARCHAR(100),
    preauth_log_id        INT REFERENCES preauth_logs(id),
    preauth_event_id      INT REFERENCES preauth_events(id),
    http_status_returned  INT,
    final_status          VARCHAR(40) NOT NULL DEFAULT 'received',
    error_message         TEXT,
    processing_time_ms    INT,
    created_at            TIMESTAMPTZ DEFAULT NOW(),
    updated_at            TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE webhook_delivery_logs ADD COLUMN IF NOT EXISTS facility_name TEXT;
ALTER TABLE webhook_delivery_logs ADD COLUMN IF NOT EXISTS insurance_no VARCHAR(100);
ALTER TABLE webhook_delivery_logs ADD COLUMN IF NOT EXISTS policy_no VARCHAR(100);
ALTER TABLE webhook_delivery_logs ADD COLUMN IF NOT EXISTS plan_name VARCHAR(100);
ALTER TABLE webhook_delivery_logs ADD COLUMN IF NOT EXISTS preauth_log_id INT REFERENCES preauth_logs(id);
ALTER TABLE webhook_delivery_logs ADD COLUMN IF NOT EXISTS preauth_event_id INT REFERENCES preauth_events(id);

-- Every agent pipeline result (for dashboard timeline)
CREATE TABLE IF NOT EXISTS agent_logs (
    id              SERIAL PRIMARY KEY,
    request_id      VARCHAR(100) NOT NULL REFERENCES preauth_logs(request_id),
    agent_num       INT NOT NULL,
    agent_name      VARCHAR(100) NOT NULL,
    status          VARCHAR(20) NOT NULL,
    result          JSONB,
    logged_at       TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_agent_logs_request_id ON agent_logs(request_id);
CREATE INDEX IF NOT EXISTS idx_agent_logs_logged_at ON agent_logs(logged_at DESC);
CREATE INDEX IF NOT EXISTS idx_preauth_logs_status ON preauth_logs(status);
CREATE INDEX IF NOT EXISTS idx_preauth_logs_org_id ON preauth_logs(org_id);
CREATE INDEX IF NOT EXISTS idx_preauth_logs_received_at ON preauth_logs(received_at DESC);
CREATE INDEX IF NOT EXISTS idx_preauth_logs_processed_at ON preauth_logs(processed_at DESC);
CREATE INDEX IF NOT EXISTS idx_preauth_events_org_id ON preauth_events(org_id);
CREATE INDEX IF NOT EXISTS idx_preauth_events_preauth_log_id ON preauth_events(preauth_log_id);
CREATE INDEX IF NOT EXISTS idx_preauth_events_event_id ON preauth_events(event_id);
CREATE INDEX IF NOT EXISTS idx_preauth_events_checkin_id ON preauth_events(checkin_id);
CREATE INDEX IF NOT EXISTS idx_preauth_events_created_at ON preauth_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_clients_org_id ON clients(org_id);
CREATE INDEX IF NOT EXISTS idx_api_clients_user_id ON api_clients(user_id);
CREATE INDEX IF NOT EXISTS idx_webhook_delivery_logs_created_at ON webhook_delivery_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_webhook_delivery_logs_org_id ON webhook_delivery_logs(org_id);
CREATE INDEX IF NOT EXISTS idx_webhook_delivery_logs_event_id ON webhook_delivery_logs(event_id);
CREATE INDEX IF NOT EXISTS idx_webhook_delivery_logs_checkin_id ON webhook_delivery_logs(checkin_id);
CREATE INDEX IF NOT EXISTS idx_webhook_delivery_logs_final_status ON webhook_delivery_logs(final_status);
CREATE INDEX IF NOT EXISTS idx_webhook_delivery_logs_preauth_log_id ON webhook_delivery_logs(preauth_log_id);
CREATE INDEX IF NOT EXISTS idx_webhook_delivery_logs_preauth_event_id ON webhook_delivery_logs(preauth_event_id);
CREATE INDEX IF NOT EXISTS idx_webhook_delivery_logs_insurance_no ON webhook_delivery_logs(insurance_no);
