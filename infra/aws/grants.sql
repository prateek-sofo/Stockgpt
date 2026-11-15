-- Read-only role for StockGPT. Run on the PRIMARY (the hub until cutover, db-1 after it); a standby
-- replicates roles and grants. The password is set by deploy.sh (never stored in the repo).
--
--   psql "$SOFO_ADMIN_URL" -v pw="'...'" -f infra/aws/grants.sql
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sofo_stockgpt') THEN
    CREATE ROLE sofo_stockgpt LOGIN;
  END IF;
END $$;
ALTER ROLE sofo_stockgpt PASSWORD :pw;
ALTER ROLE sofo_stockgpt SET default_transaction_read_only = on;
ALTER ROLE sofo_stockgpt SET statement_timeout = '15min';
ALTER ROLE sofo_stockgpt CONNECTION LIMIT 2;

GRANT CONNECT ON DATABASE sofo_world TO sofo_stockgpt;
GRANT USAGE ON SCHEMA public TO sofo_stockgpt;
-- Only what the news export reads: articles, their company mentions, company identifiers, events.
GRANT SELECT ON news_articles, article_mentions, entity_identifiers, events TO sofo_stockgpt;
