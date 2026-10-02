-- Raw table for the "Localization Plan" Google Sheet. Run in BigQuery with location asia-south1.
-- Append-only: every run stores a full copy of the sheet, values exactly as typed (all text).
-- Cleaning (language/grade mapping, date parsing, rejects, dedupe) happens in staging.

CREATE TABLE IF NOT EXISTS `learning-reach-473210.raw.localization_plan_snapshot` (
  plan_id STRING,
  language STRING,  -- as typed, e.g. "Marathi ", "marathi", "MR", "Oriya"
  board STRING,
  grade STRING,  -- as typed, e.g. "Class 6", "6", "VII"
  subject STRING,
  resources_planned STRING,  -- text on purpose: may contain "forty"
  owner STRING,
  status STRING,  -- may be NULL (blank cell)
  target_date STRING,  -- YYYY-MM-DD or DD/MM/YYYY
  last_updated STRING,
  notes STRING,
  _row_number INT64,  -- row number in the sheet (header = 1)
  row_json STRING,  -- the full row keyed by header, incl. any extra columns
  _sheet_id STRING,
  _load_id STRING,
  _ingested_at TIMESTAMP
)
PARTITION BY DATE(_ingested_at);
