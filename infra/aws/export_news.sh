#!/usr/bin/env bash
# Export the Sofo World tables StockGPT reads (news, mentions, identifiers, events, relationships) to S3 or a folder.
# Run it anywhere the company database is reachable: the hub, cc-1, or the Mac through the :5434 tunnel.
# Read-only: five COPY ... TO STDOUT statements inside a read-only transaction. Needs psql (and aws for s3://).
#
#   SOFO_DATABASE_URL=postgresql://...@127.0.0.1:5434/sofo_world \
#     infra/aws/export_news.sh s3://<bucket>/stockgpt/sofo-dump        # or a local folder: ./sofo-dump
#   SINCE=2018-01-01 (default) limits articles and events by date.
#
# Then, where StockGPT runs:
#   SOFO_NEWS_URI=s3://<bucket>/stockgpt/sofo-dump python -m stockgpt.sources.sofo_world      # news, events, competitor graph
#   SOFO_NEWS_URI=s3://<bucket>/stockgpt/sofo-dump python -m stockgpt.sources.sofo_finance    # financials, filings, earnings dates
set -euo pipefail
DEST=${1:?destination: s3://bucket/prefix or a local folder}
: "${SOFO_DATABASE_URL:?set SOFO_DATABASE_URL (any role that can read the five tables)}"
SINCE=${SINCE:-2018-01-01}
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

dump() {   # name, query
  echo "exporting $1"
  PGOPTIONS="-c default_transaction_read_only=on -c statement_timeout=0" \
    psql "$SOFO_DATABASE_URL" -v ON_ERROR_STOP=1 -q -c "\copy ($2) TO STDOUT WITH (FORMAT csv, HEADER true)" | gzip > "$TMP/$1.csv.gz"
  if [[ $DEST == s3://* ]]; then aws s3 cp --only-show-errors "$TMP/$1.csv.gz" "$DEST/$1/part-000.csv.gz"
  else mkdir -p "$DEST/$1" && mv "$TMP/$1.csv.gz" "$DEST/$1/part-000.csv.gz"; fi
}

dump news_articles "SELECT article_id, url, title, snippet, published_at, first_seen, publisher_domain, publisher_name, source_id, cluster_id, is_press_release FROM news_articles WHERE published_at >= '$SINCE'"
dump article_mentions "SELECT m.article_id, m.entity_type, m.entity_id, m.salience, m.is_subject FROM article_mentions m JOIN news_articles a USING (article_id) WHERE m.entity_type = 'company' AND a.published_at >= '$SINCE'"
dump entity_identifiers "SELECT entity_type, entity_id, id_type, id_value, confidence FROM entity_identifiers WHERE entity_type = 'company' AND id_type IN ('ticker', 'cik')"
dump events "SELECT sofo_event_id, event_type, subject_type, subject_id, event_date, detected_at, title, source_id, source_url, confidence, article_id FROM events WHERE subject_type = 'company' AND event_date >= '$SINCE'"
dump relationships "SELECT from_type, from_id, to_type, to_id, rel_type, confidence, first_seen, valid_to, source_id FROM relationships WHERE from_type = 'company' AND to_type = 'company' AND rel_type IN ('competitor', 'customer', 'supplier', 'partner')"
# financials and filings from the SEC crawlers (public rows only): metric points with their filing date, filing records
dump observations "SELECT coalesce(o.entity_ref->>'cik', (SELECT id_value FROM entity_identifiers i WHERE i.entity_id = o.entity_id AND i.entity_type = 'company' AND i.id_type = 'cik' ORDER BY i.confidence DESC LIMIT 1)) AS cik, o.field, o.value, o.published_at, o.source_id FROM observations o WHERE o.entity_type = 'company' AND o.license_scope = 'public' AND ((o.field LIKE 'metric.%' AND o.source_id = 'FN01') OR o.field = 'filing')"
echo "done -> $DEST"
