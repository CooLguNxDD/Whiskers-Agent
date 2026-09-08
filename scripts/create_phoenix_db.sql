-- Idempotent Phoenix role + database creation, run once against the existing
-- postgres_data volume (postgres only runs docker-entrypoint-initdb.d scripts
-- on a FRESH data directory, and this project's dev volume is not fresh).
--
-- Usage:
--   docker exec -i whiskers-postgres psql -U whiskers -d postgres -f - < scripts/create_phoenix_db.sql
--
-- Deliberately a SEPARATE database (never the app's whiskers_mcp) so Phoenix's
-- own tables never collide with Alembic autogenerate or scripts/reseed.py.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'phoenix') THEN
        CREATE ROLE phoenix WITH LOGIN PASSWORD 'phoenix';
    END IF;
END
$$;

SELECT 'CREATE DATABASE phoenix OWNER phoenix'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'phoenix')
\gexec
