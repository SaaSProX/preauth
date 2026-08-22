-- SAA: Speed up the /auth/patients "latest payload per patient" lookup
-- The correlated subquery in auth/router.py (qa/patients endpoint) does
-- ORDER BY received_at DESC LIMIT 1 per patient_id within an org. The
-- existing idx_preauth_logs_org_patient (org_id, patient_id) narrows to the
-- right rows but still requires a sort; adding received_at lets Postgres
-- satisfy that ORDER BY ... LIMIT 1 via an index scan instead.
-- Run with: psql $DATABASE_URL -f migrations/004_add_patient_received_index.sql

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_preauth_logs_org_patient_received
    ON preauth_logs(org_id, patient_id, received_at DESC);
