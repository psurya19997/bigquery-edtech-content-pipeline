-- Raw tables for national education indicators. Run in BigQuery with location asia-south1.
-- Append-only: each monthly run reloads the full history; staging keeps the latest _load_id.
-- Every field the APIs return is stored, plus the original record as JSON (record_json).

CREATE TABLE IF NOT EXISTS `learning-reach-473210.raw.worldbank_indicators` (
  indicator_id STRING,  -- e.g. SE.PRM.CMPT.ZS
  indicator_name STRING,  -- e.g. Primary completion rate, total (% of relevant age group)
  country_id STRING,  -- 2-letter code, e.g. IN
  country_name STRING,  -- e.g. India
  country_iso3 STRING,  -- 3-letter code: IND, BGD, PAK, NPL, LKA
  year INT64,
  value FLOAT64,  -- NULL for years with no data; gross ratios can exceed 100
  unit STRING,
  obs_status STRING,
  decimal INT64,
  source_last_updated STRING,  -- when the World Bank last updated this indicator
  record_json STRING,  -- the original API record, untouched
  _load_id STRING,
  _ingested_at TIMESTAMP
)
PARTITION BY DATE(_ingested_at);

CREATE TABLE IF NOT EXISTS `learning-reach-473210.raw.uis_indicators` (
  indicator_id STRING,  -- e.g. CR.1
  indicator_name STRING,  -- e.g. Completion rate, primary education, both sexes (%)
  geo_unit STRING,  -- IND, BGD, PAK, NPL, LKA
  year INT64,
  value FLOAT64,
  magnitude STRING,
  qualifier STRING,
  indicator_theme STRING,  -- e.g. EDUCATION
  last_data_update STRING,  -- e.g. 02/09/2026
  last_data_update_description STRING,  -- e.g. February 2026 Data Release
  indicator_metadata_json STRING,  -- full indicator metadata incl. glossary terms
  record_json STRING,  -- the original API record, untouched
  _load_id STRING,
  _ingested_at TIMESTAMP
)
PARTITION BY DATE(_ingested_at);
