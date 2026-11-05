-- Database users with least privilege. Safe to run again (make db-roles).
-- Run as the admin user. Passwords come in as psql variables: etl_password, api_password.
--
--   orisun_etl  the pipeline: owns oncology, quality, catalog; no access to auth
--   orisun_api  the API: read-only on everything it serves, plus auth.users for logins
--
-- Per-user limits are set here, in the database, so they hold even if app code is wrong.
\set ON_ERROR_STOP on

SELECT format('CREATE ROLE orisun_etl LOGIN PASSWORD %L', :'etl_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'orisun_etl') \gexec
SELECT format('CREATE ROLE orisun_api LOGIN PASSWORD %L', :'api_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'orisun_api') \gexec
ALTER ROLE orisun_etl PASSWORD :'etl_password';
ALTER ROLE orisun_api PASSWORD :'api_password';
-- On RDS the admin is not a superuser: it must be a member of orisun_etl to hand schemas to it
-- and to set its default privileges below. (Locally the admin is a superuser; this is a no-op.)
GRANT orisun_etl TO CURRENT_USER;

-- Bulkheads: each side has its own connection budget, so one can't starve the other.
ALTER ROLE orisun_etl CONNECTION LIMIT 20;
ALTER ROLE orisun_api CONNECTION LIMIT 20;
-- The API is read-only and fast-fail: no writes, no query longer than 5 s, no stuck sessions.
ALTER ROLE orisun_api SET default_transaction_read_only = on;
ALTER ROLE orisun_api SET statement_timeout = '5s';
ALTER ROLE orisun_api SET idle_in_transaction_session_timeout = '30s';
-- The pipeline gets longer, but still bounded, limits.
ALTER ROLE orisun_etl SET statement_timeout = '15min';
ALTER ROLE orisun_etl SET idle_in_transaction_session_timeout = '10min';

SELECT format('GRANT CONNECT, TEMPORARY ON DATABASE %I TO orisun_etl, orisun_api',
              current_database()) \gexec
SELECT format('GRANT CREATE ON DATABASE %I TO orisun_etl', current_database()) \gexec
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- Pipeline schemas: owned by orisun_etl (tables created earlier by the admin are handed over).
CREATE SCHEMA IF NOT EXISTS oncology AUTHORIZATION orisun_etl;
CREATE SCHEMA IF NOT EXISTS quality AUTHORIZATION orisun_etl;
CREATE SCHEMA IF NOT EXISTS catalog AUTHORIZATION orisun_etl;
ALTER SCHEMA oncology OWNER TO orisun_etl;
ALTER SCHEMA quality OWNER TO orisun_etl;
ALTER SCHEMA catalog OWNER TO orisun_etl;
DO $$
DECLARE t record;
BEGIN
    FOR t IN SELECT schemaname, tablename FROM pg_tables
             WHERE schemaname IN ('oncology', 'quality', 'catalog')
               AND tableowner <> 'orisun_etl'
    LOOP
        EXECUTE format('ALTER TABLE %I.%I OWNER TO orisun_etl', t.schemaname, t.tablename);
    END LOOP;
END $$;

-- The API can read every pipeline table, including ones the pipeline creates later.
GRANT USAGE ON SCHEMA oncology, quality, catalog TO orisun_api;
GRANT SELECT ON ALL TABLES IN SCHEMA oncology, quality, catalog TO orisun_api;
ALTER DEFAULT PRIVILEGES FOR ROLE orisun_etl IN SCHEMA oncology, quality, catalog
    GRANT SELECT ON TABLES TO orisun_api;

-- Logins: owned by the admin (user CLI). The API may read it; the pipeline may not.
CREATE SCHEMA IF NOT EXISTS auth;
REVOKE ALL ON SCHEMA auth FROM PUBLIC;
GRANT USAGE ON SCHEMA auth TO orisun_api;
GRANT SELECT ON ALL TABLES IN SCHEMA auth TO orisun_api;
ALTER DEFAULT PRIVILEGES IN SCHEMA auth GRANT SELECT ON TABLES TO orisun_api;
