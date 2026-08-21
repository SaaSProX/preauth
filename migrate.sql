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
-- Mode the agent was operating in when we wrote back to AMAN. Optional cache
-- for the Accuracy Dashboard mode toggle (All / Advisory / Applied). Falls
-- back to 'advisory' when NULL, matching the live default.
ALTER TABLE preauth_logs ADD COLUMN IF NOT EXISTS callback_mode TEXT;
-- NOTE: AMAN's per-item decisions are NOT stored as columns here. They live
-- in preauth_events.raw_payload.pa_items[].status (0=pending, 1=approved,
-- 2=queried, 3=rejected), confirmed by Sakeenah (AMAN) on 2026-06-01. The
-- Accuracy Dashboard reads them directly via the latest event per checkin
-- (see /auth/qa/accuracy in auth/router.py). No ingestion endpoint is
-- needed — the data already arrives on every pa.submitted webhook.
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
    model           TEXT,
    input_tokens    INT,
    output_tokens   INT,
    total_tokens    INT,
    estimated_cost_usd NUMERIC(12, 6),
    model_usage     JSONB,
    logged_at       TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE agent_logs ADD COLUMN IF NOT EXISTS model TEXT;
ALTER TABLE agent_logs ADD COLUMN IF NOT EXISTS input_tokens INT;
ALTER TABLE agent_logs ADD COLUMN IF NOT EXISTS output_tokens INT;
ALTER TABLE agent_logs ADD COLUMN IF NOT EXISTS total_tokens INT;
ALTER TABLE agent_logs ADD COLUMN IF NOT EXISTS estimated_cost_usd NUMERIC(12, 6);
ALTER TABLE agent_logs ADD COLUMN IF NOT EXISTS model_usage JSONB;

-- Append-only audit trail for compliance-sensitive UI actions (PDF exports,
-- override events, drill-in viewing, etc.). One row per recorded event.
CREATE TABLE IF NOT EXISTS audit_events (
    id              SERIAL PRIMARY KEY,
    org_id          INT REFERENCES organizations(id),
    user_id         INT REFERENCES clients(id),
    user_email      VARCHAR(200),
    event_type      VARCHAR(50) NOT NULL,   -- 'pdf_download', etc.
    target_kind     VARCHAR(50),            -- 'patient' | 'pa' | 'org'
    target_id       VARCHAR(200),           -- the patient_id / request_id / org_id
    metadata        JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Gmail / Google Workspace connections for managed support inbox intake.
-- Tokens are never returned to the dashboard; UI only sees mailbox/status
-- metadata. One organization can connect more than one mailbox later.
CREATE TABLE IF NOT EXISTS gmail_connections (
    id                      SERIAL PRIMARY KEY,
    org_id                  INT NOT NULL REFERENCES organizations(id),
    connected_by            INT REFERENCES clients(id),
    provider                VARCHAR(30) NOT NULL DEFAULT 'google',
    email                   VARCHAR(255) NOT NULL,
    scopes                  JSONB DEFAULT '[]'::jsonb,
    access_token            TEXT,
    refresh_token           TEXT,
    token_expiry            TIMESTAMPTZ,
    status                  VARCHAR(30) NOT NULL DEFAULT 'connected',
    watch_history_id        VARCHAR(100),
    watch_expiration        TIMESTAMPTZ,
    watch_status            VARCHAR(30) NOT NULL DEFAULT 'not_started',
    watch_started_at        TIMESTAMPTZ,
    watch_last_notification_at TIMESTAMPTZ,
    watch_error             TEXT,
    last_sync_at            TIMESTAMPTZ,
    last_error              TEXT,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_gmail_connections_org_provider_email
ON gmail_connections(org_id, provider, email);

ALTER TABLE gmail_connections ADD COLUMN IF NOT EXISTS watch_history_id VARCHAR(100);
ALTER TABLE gmail_connections ADD COLUMN IF NOT EXISTS watch_expiration TIMESTAMPTZ;
ALTER TABLE gmail_connections ADD COLUMN IF NOT EXISTS watch_status VARCHAR(30) NOT NULL DEFAULT 'not_started';
ALTER TABLE gmail_connections ADD COLUMN IF NOT EXISTS watch_started_at TIMESTAMPTZ;
ALTER TABLE gmail_connections ADD COLUMN IF NOT EXISTS watch_last_notification_at TIMESTAMPTZ;
ALTER TABLE gmail_connections ADD COLUMN IF NOT EXISTS watch_error TEXT;

-- Normalized email intake records created from Gmail history changes.
-- Pub/Sub only sends an email address + historyId, so the backend fetches
-- message details from Gmail and stores a durable local support queue here.
CREATE TABLE IF NOT EXISTS support_messages (
    id                      SERIAL PRIMARY KEY,
    org_id                  INT NOT NULL REFERENCES organizations(id),
    gmail_connection_id     INT REFERENCES gmail_connections(id),
    provider                VARCHAR(30) NOT NULL DEFAULT 'google',
    mailbox_email           VARCHAR(255),
    gmail_message_id        VARCHAR(255) NOT NULL,
    gmail_thread_id         VARCHAR(255),
    history_id              VARCHAR(100),
    from_email              TEXT,
    to_email                TEXT,
    subject                 TEXT,
    snippet                 TEXT,
    body_text               TEXT,
    internal_date           TIMESTAMPTZ,
    received_at             TIMESTAMPTZ,
    label_ids               JSONB DEFAULT '[]'::jsonb,
    status                  VARCHAR(30) NOT NULL DEFAULT 'new',
    raw_payload             JSONB,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (org_id, provider, gmail_message_id)
);

-- Append-only Pub/Sub delivery log for Gmail notifications. This is separate
-- from support_messages because one Pub/Sub notification can contain zero,
-- one, or many new Gmail messages after history sync.
CREATE TABLE IF NOT EXISTS gmail_notification_logs (
    id                      SERIAL PRIMARY KEY,
    org_id                  INT REFERENCES organizations(id),
    gmail_connection_id     INT REFERENCES gmail_connections(id),
    email                   VARCHAR(255),
    history_id              VARCHAR(100),
    pubsub_message_id       VARCHAR(255),
    subscription            TEXT,
    raw_payload             JSONB,
    decoded_payload         JSONB,
    processed_status        VARCHAR(40) NOT NULL DEFAULT 'received',
    error_message           TEXT,
    message_count           INT NOT NULL DEFAULT 0,
    received_at             TIMESTAMPTZ DEFAULT NOW(),
    processed_at            TIMESTAMPTZ,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_audit_events_org_id ON audit_events(org_id);
CREATE INDEX IF NOT EXISTS idx_audit_events_created_at ON audit_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_events_event_type ON audit_events(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_events_target ON audit_events(target_kind, target_id);
CREATE INDEX IF NOT EXISTS idx_gmail_connections_org_id ON gmail_connections(org_id);
CREATE INDEX IF NOT EXISTS idx_gmail_connections_status ON gmail_connections(status);
CREATE INDEX IF NOT EXISTS idx_gmail_connections_watch_status ON gmail_connections(watch_status);
CREATE INDEX IF NOT EXISTS idx_support_messages_org_id ON support_messages(org_id);
CREATE INDEX IF NOT EXISTS idx_support_messages_gmail_connection_id ON support_messages(gmail_connection_id);
CREATE INDEX IF NOT EXISTS idx_support_messages_received_at ON support_messages(received_at DESC);
CREATE INDEX IF NOT EXISTS idx_support_messages_status ON support_messages(status);
CREATE INDEX IF NOT EXISTS idx_gmail_notification_logs_org_id ON gmail_notification_logs(org_id);
CREATE INDEX IF NOT EXISTS idx_gmail_notification_logs_connection_id ON gmail_notification_logs(gmail_connection_id);
CREATE INDEX IF NOT EXISTS idx_gmail_notification_logs_received_at ON gmail_notification_logs(received_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_logs_request_id ON agent_logs(request_id);
CREATE INDEX IF NOT EXISTS idx_agent_logs_logged_at ON agent_logs(logged_at DESC);
CREATE INDEX IF NOT EXISTS idx_preauth_logs_status ON preauth_logs(status);
CREATE INDEX IF NOT EXISTS idx_preauth_logs_org_id ON preauth_logs(org_id);
CREATE INDEX IF NOT EXISTS idx_preauth_logs_received_at ON preauth_logs(received_at DESC);
CREATE INDEX IF NOT EXISTS idx_preauth_logs_processed_at ON preauth_logs(processed_at DESC);
-- Index on callback_mode so the Accuracy Dashboard's per-mode aggregates
-- (all / advisory / applied) stay snappy as preauth_logs grows. AMAN's
-- finals are read from preauth_events.raw_payload.pa_items[].status, not
-- from preauth_logs, so no dedicated AMAN index is needed here.
CREATE INDEX IF NOT EXISTS idx_preauth_logs_callback_mode ON preauth_logs(callback_mode);
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

-- PA comments/feedback from HMO team members
CREATE TABLE IF NOT EXISTS pa_comments (
    id              SERIAL PRIMARY KEY,
    org_id          INT NOT NULL REFERENCES organizations(id),
    request_id      VARCHAR(100) NOT NULL,
    user_id         INT REFERENCES clients(id),
    user_email      VARCHAR(200),
    user_name       VARCHAR(200),
    comment_text    TEXT NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pa_comments_org_id ON pa_comments(org_id);
CREATE INDEX IF NOT EXISTS idx_pa_comments_request_id ON pa_comments(request_id);
CREATE INDEX IF NOT EXISTS idx_pa_comments_created_at ON pa_comments(created_at DESC);

-- Mismatch review workflow (SAA-54)
-- Tracks classification and resolution of agent/AMAN disagreements
CREATE TABLE IF NOT EXISTS mismatch_reviews (
    id                    SERIAL PRIMARY KEY,
    org_id                INT REFERENCES organizations(id),
    request_id            VARCHAR(100) NOT NULL,
    checkin_id            VARCHAR(100),
    reviewer_id           INT REFERENCES clients(id),
    reviewer_email        VARCHAR(200),
    
    -- Classification
    mismatch_type         VARCHAR(50) NOT NULL,  -- decision | amount | coverage | eligibility
    cause_category        VARCHAR(50),           -- rule_gap | missing_data | plan_ambiguity | pricing | clinical | aman_override | other
    
    -- Details
    agent_decision        VARCHAR(20),
    agent_amount          NUMERIC,
    aman_decision         VARCHAR(20),
    aman_amount           NUMERIC,
    amount_delta          NUMERIC,
    
    -- Review
    notes                 TEXT,
    follow_up_action      TEXT,
    fix_status            VARCHAR(30) DEFAULT 'open',  -- open | in_progress | fixed | accepted_difference | wont_fix
    
    -- Timestamps
    created_at            TIMESTAMPTZ DEFAULT NOW(),
    updated_at            TIMESTAMPTZ DEFAULT NOW(),
    resolved_at           TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_mismatch_reviews_org ON mismatch_reviews(org_id);
CREATE INDEX IF NOT EXISTS idx_mismatch_reviews_request ON mismatch_reviews(request_id);
CREATE INDEX IF NOT EXISTS idx_mismatch_reviews_status ON mismatch_reviews(fix_status);
CREATE INDEX IF NOT EXISTS idx_mismatch_reviews_cause ON mismatch_reviews(cause_category);

-- ============================================================================
-- SAA-85: Performance indexes for common query patterns
-- ============================================================================

-- Preauth logs: patient history and dashboard queries
CREATE INDEX IF NOT EXISTS idx_preauth_logs_patient_id ON preauth_logs(patient_id);
CREATE INDEX IF NOT EXISTS idx_preauth_logs_org_patient ON preauth_logs(org_id, patient_id);
CREATE INDEX IF NOT EXISTS idx_preauth_logs_decision ON preauth_logs(decision);
CREATE INDEX IF NOT EXISTS idx_preauth_logs_org_received ON preauth_logs(org_id, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_preauth_logs_callback_status ON preauth_logs(callback_status);

-- Preauth events: event type filtering and joins
CREATE INDEX IF NOT EXISTS idx_preauth_events_event_type ON preauth_events(event_type);
CREATE INDEX IF NOT EXISTS idx_preauth_events_request_id ON preauth_events(request_id);
CREATE INDEX IF NOT EXISTS idx_preauth_events_org_type_created ON preauth_events(org_id, event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_preauth_events_org_checkin ON preauth_events(org_id, checkin_id);

-- Agent logs: latency calculations
CREATE INDEX IF NOT EXISTS idx_agent_logs_agent_num ON agent_logs(agent_num);
CREATE INDEX IF NOT EXISTS idx_agent_logs_request_agent ON agent_logs(request_id, agent_num);

-- Webhook delivery logs: duplicate tracking
CREATE INDEX IF NOT EXISTS idx_webhook_delivery_logs_db_status ON webhook_delivery_logs(db_insert_status);
CREATE INDEX IF NOT EXISTS idx_webhook_delivery_logs_org_status_created ON webhook_delivery_logs(org_id, db_insert_status, created_at DESC);

-- Clients: email and api_clients.api_key are UNIQUE constraints already
-- backed by auto-created unique indexes - no separate index needed.
CREATE INDEX IF NOT EXISTS idx_api_clients_org_active ON api_clients(org_id, is_active);
