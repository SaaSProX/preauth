-- Our PostgreSQL schema
-- Run once to set up the dashboard DB

-- Every incoming webhook request
CREATE TABLE IF NOT EXISTS preauth_logs (
    id              SERIAL PRIMARY KEY,
    request_id      VARCHAR(100) NOT NULL UNIQUE,
    patient_id      VARCHAR(100) NOT NULL,
    received_at     TIMESTAMP DEFAULT NOW(),
    status          VARCHAR(20) DEFAULT 'processing'  -- processing | completed | failed
);

-- Every agent tool call (for dashboard timeline)
CREATE TABLE IF NOT EXISTS agent_steps (
    id              SERIAL PRIMARY KEY,
    request_id      VARCHAR(100) NOT NULL REFERENCES preauth_logs(request_id),
    step_number     INT NOT NULL,
    tool_name       VARCHAR(100) NOT NULL,
    tool_input      JSONB,
    tool_result     JSONB,
    executed_at     TIMESTAMP DEFAULT NOW()
);

-- Final decision per request
CREATE TABLE IF NOT EXISTS decisions (
    id              SERIAL PRIMARY KEY,
    request_id      VARCHAR(100) NOT NULL UNIQUE REFERENCES preauth_logs(request_id),
    patient_id      VARCHAR(100) NOT NULL,
    decision        VARCHAR(20) NOT NULL,   -- approved | denied | escalated
    reason          TEXT NOT NULL,
    decided_at      TIMESTAMP DEFAULT NOW()
);

-- Emails sent
CREATE TABLE IF NOT EXISTS notifications (
    id              SERIAL PRIMARY KEY,
    request_id      VARCHAR(100) NOT NULL REFERENCES preauth_logs(request_id),
    sent_to         VARCHAR(255) NOT NULL,
    subject         VARCHAR(255),
    body            TEXT,
    sent_at         TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS api_clients (
    id          SERIAL PRIMARY KEY,
    client_name VARCHAR(100) NOT NULL,
    api_key     VARCHAR(100) NOT NULL UNIQUE,
    is_active   BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMP DEFAULT NOW()
);

-- Indexes for dashboard queries
CREATE INDEX IF NOT EXISTS idx_agent_steps_request_id ON agent_steps(request_id);
CREATE INDEX IF NOT EXISTS idx_decisions_decided_at ON decisions(decided_at DESC);
CREATE INDEX IF NOT EXISTS idx_preauth_logs_status ON preauth_logs(status);