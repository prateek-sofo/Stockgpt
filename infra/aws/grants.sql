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
-- Only what StockGPT reads: articles, their company mentions, company identifiers, events, and the
-- company relationship graph (competitor / customer / supplier / partner).
GRANT SELECT ON news_articles, article_mentions, entity_identifiers, events, relationships TO sofo_stockgpt;

-- Financials and filings: StockGPT reads the SEC crawlers' output, public rows only, through a view,
-- so the role never sees the rest of the observation log (vendor or customer rows, other fields).
CREATE OR REPLACE VIEW stockgpt_sec_observations AS
SELECT coalesce(o.entity_ref->>'cik', c.id_value) AS cik, o.field, o.value, o.published_at, o.source_id
FROM observations o
LEFT JOIN LATERAL (
  SELECT id_value FROM entity_identifiers i
  WHERE i.entity_id = o.entity_id AND i.entity_type = 'company' AND i.id_type = 'cik'
  ORDER BY i.confidence DESC LIMIT 1) c ON true
WHERE o.entity_type = 'company' AND o.license_scope = 'public'
  AND ((o.field LIKE 'metric.%' AND o.source_id = 'FN01') OR o.field = 'filing');
GRANT SELECT ON stockgpt_sec_observations TO sofo_stockgpt;
