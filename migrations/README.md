# Database Migrations

This folder contains incremental migrations for existing deployments.

## Running Migrations

For existing deployments, run migrations in order:

```bash
# Connect to your database
psql $DATABASE_URL

# Run migration
\i migrations/003_add_performance_indexes.sql
```

Or via command line:
```bash
psql $DATABASE_URL -f migrations/003_add_performance_indexes.sql
```

## Migration Files

| File | Description |
|------|-------------|
| `003_add_performance_indexes.sql` | SAA-85: Adds indexes for dashboard and QA query performance |

## New Deployments

For new deployments, use `migrate.sql` which includes all indexes.

## Notes

- Migrations use `CREATE INDEX CONCURRENTLY` to avoid locking tables
- Migrations use `IF NOT EXISTS` to be idempotent (safe to run multiple times)
- Always backup your database before running migrations in production
