-- Raw + ops tables for DIKSHA ingestion. Run in BigQuery with location asia-south1.
-- Raw tables are append-only: every run adds rows; staging later keeps the latest per identifier.
-- Timestamps stay as STRING in raw (DIKSHA format "2018-10-16T14:43:22.143+0000"); staging parses them.

CREATE TABLE IF NOT EXISTS `learning-reach-473210.raw.diksha_content` (
  identifier STRING,
  name STRING,
  board STRING,
  medium ARRAY<STRING>,
  gradeLevel ARRAY<STRING>,
  subject ARRAY<STRING>,
  language ARRAY<STRING>,
  audience ARRAY<STRING>,
  primaryCategory STRING,
  contentType STRING,
  mimeType STRING,
  objectType STRING,
  license STRING,
  channel STRING,
  framework STRING,
  createdOn STRING,
  lastUpdatedOn STRING,
  lastPublishedOn STRING,
  play_sessions_json STRING,  -- e.g. {"portal": 4133}
  time_spent_json STRING,
  avg_rating FLOAT64,
  ratings_count INT64,
  downloads INT64,
  slice_id STRING,
  _mode STRING,  -- full | incremental
  _load_id STRING,
  _ingested_at TIMESTAMP
)
PARTITION BY DATE(_ingested_at)
CLUSTER BY identifier;

CREATE TABLE IF NOT EXISTS `learning-reach-473210.raw.diksha_metrics_snapshot` (
  snapshot_date DATE,
  identifier STRING,
  play_sessions_json STRING,
  time_spent_json STRING,
  avg_rating FLOAT64,
  ratings_count INT64,
  downloads INT64,
  _load_id STRING,
  _ingested_at TIMESTAMP
)
PARTITION BY snapshot_date
CLUSTER BY identifier;

CREATE TABLE IF NOT EXISTS `learning-reach-473210.ops.ingestion_log` (
  run_id STRING,
  source STRING,
  mode STRING,
  started_at TIMESTAMP,
  finished_at TIMESTAMP,
  status STRING,  -- success | failed
  rows_loaded INT64,
  slices INT64,
  watermark STRING,  -- lastUpdatedOn the incremental run fetched from
  error_message STRING
)
PARTITION BY DATE(started_at);
