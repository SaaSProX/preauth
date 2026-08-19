-- SAA-85: Add performance indexes
-- These indexes target the most common query patterns identified in auth/router.py
-- Run with: psql $DATABASE_URL -f migrations/003_add_performance_indexes.sql

-- ============================================================================
-- PREAUTH_LOGS INDEXES
-- ============================================================================

-- Patient history queries: WHERE org_id = $1 AND patient_id = $2
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_preauth_logs_patient_id 
    ON preauth_logs(patient_id);

-- Composite for patient history (org + patient)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_preauth_logs_org_patient 
    ON preauth_logs(org_id, patient_id);

-- Dashboard filtering by decision
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_preauth_logs_decision 
    ON preauth_logs(decision);

-- Composite for dashboard main query (org + received_at for pagination)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_preauth_logs_org_received 
    ON preauth_logs(org_id, received_at DESC);

-- Callback status queries (accuracy dashboard filters)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_preauth_logs_callback_status 
    ON preauth_logs(callback_status);

-- ============================================================================
-- PREAUTH_EVENTS INDEXES
-- ============================================================================

-- Event type filtering (frequent 'pa.submitted', 'pa.approved' lookups)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_preauth_events_event_type 
    ON preauth_events(event_type);

-- Join with preauth_logs by request_id
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_preauth_events_request_id 
    ON preauth_events(request_id);

-- Composite for event summary queries (org + event_type + created_at)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_preauth_events_org_type_created 
    ON preauth_events(org_id, event_type, created_at DESC);

-- Composite for checkin lookups within org
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_preauth_events_org_checkin 
    ON preauth_events(org_id, checkin_id);

-- ============================================================================
-- AGENT_LOGS INDEXES
-- ============================================================================

-- Agent number filtering (latency calculations filter on agent_num = 1)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_agent_logs_agent_num 
    ON agent_logs(agent_num);

-- Composite for latency queries (request_id + agent_num)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_agent_logs_request_agent 
    ON agent_logs(request_id, agent_num);

-- ============================================================================
-- WEBHOOK_DELIVERY_LOGS INDEXES
-- ============================================================================

-- Duplicate event tracking (db_insert_status = 'duplicate_event_seen')
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_webhook_delivery_logs_db_status 
    ON webhook_delivery_logs(db_insert_status);

-- Composite for duplicate summary queries (org + status + created_at)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_webhook_delivery_logs_org_status_created 
    ON webhook_delivery_logs(org_id, db_insert_status, created_at DESC);

-- ============================================================================
-- CLIENTS INDEXES
-- ============================================================================

-- Note: no separate email index needed here - clients.email has a UNIQUE
-- constraint, which Postgres already backs with an auto-created unique
-- btree index that serves `WHERE email = $1` lookups just as well.

-- Active clients filter
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_clients_active 
    ON clients(is_active) WHERE is_active = TRUE;

-- ============================================================================
-- API_CLIENTS INDEXES
-- ============================================================================

-- Note: no separate api_key index needed here - api_clients.api_key has a
-- UNIQUE constraint, already backed by an auto-created unique btree index.

-- Active API clients
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_api_clients_org_active 
    ON api_clients(org_id, is_active) WHERE is_active = TRUE;

-- ============================================================================
-- VERIFY INDEXES EXIST
-- ============================================================================

-- Run this to verify all indexes were created:
-- SELECT indexname FROM pg_indexes WHERE tablename IN 
--   ('preauth_logs', 'preauth_events', 'agent_logs', 'webhook_delivery_logs', 'clients', 'api_clients')
-- ORDER BY tablename, indexname;
