-- Raw tables for YouTube Data API v3. Run in BigQuery with location asia-south1.
-- Append-only. Timestamps stay as STRING in raw (ISO 8601, e.g. 2026-09-29T10:00:00Z).

-- One row per video, written when the video is first seen (or on refresh_details=true).
CREATE TABLE IF NOT EXISTS `learning-reach-473210.raw.youtube_videos` (
  video_id STRING,
  etag STRING,  -- changes when YouTube's copy of the video resource changes
  channel_id STRING,
  channel_title STRING,
  published_at STRING,
  title STRING,  -- e.g. "Bayes' theorem [Hinglish] | Grade 12 | Math"
  description STRING,  -- often links to the matching practice exercise
  tags ARRAY<STRING>,
  category_id STRING,  -- 27 = Education
  default_language STRING,
  default_audio_language STRING,  -- e.g. hi, en
  live_broadcast_content STRING,
  duration STRING,  -- ISO 8601, e.g. PT12M34S
  duration_seconds INT64,
  dimension STRING,
  definition STRING,  -- hd | sd
  caption BOOL,  -- has captions/subtitles
  licensed_content BOOL,
  projection STRING,
  content_rating_json STRING,
  region_restriction_json STRING,
  upload_status STRING,
  privacy_status STRING,
  license STRING,  -- youtube | creativeCommon
  embeddable BOOL,
  public_stats_viewable BOOL,
  made_for_kids BOOL,
  topic_categories ARRAY<STRING>,  -- Wikipedia URLs, e.g. .../wiki/Mathematics
  localizations_json STRING,  -- translated titles/descriptions per language
  recording_date STRING,
  view_count INT64,  -- totals at the moment the row was written
  like_count INT64,
  comment_count INT64,
  record_json STRING,  -- the original API resource, untouched
  _load_id STRING,
  _ingested_at TIMESTAMP
)
PARTITION BY DATE(_ingested_at)
CLUSTER BY video_id;

-- Daily photo of every video's counters: views on day X = total(X) - total(X-1).
CREATE TABLE IF NOT EXISTS `learning-reach-473210.raw.youtube_video_stats_snapshot` (
  snapshot_date DATE,
  video_id STRING,
  channel_id STRING,
  view_count INT64,
  like_count INT64,  -- NULL when the owner hides likes
  comment_count INT64,  -- NULL when comments are off
  _load_id STRING,
  _ingested_at TIMESTAMP
)
PARTITION BY snapshot_date
CLUSTER BY video_id;

-- Daily photo of each channel's totals.
CREATE TABLE IF NOT EXISTS `learning-reach-473210.raw.youtube_channel_snapshot` (
  snapshot_date DATE,
  channel_id STRING,
  title STRING,
  description STRING,
  custom_url STRING,  -- @handle
  published_at STRING,
  country STRING,
  default_language STRING,
  view_count INT64,
  subscriber_count INT64,  -- rounded by YouTube to 3 significant figures
  hidden_subscriber_count BOOL,
  video_count INT64,
  uploads_playlist_id STRING,
  keywords STRING,
  topic_categories ARRAY<STRING>,
  privacy_status STRING,
  made_for_kids BOOL,
  record_json STRING,
  _load_id STRING,
  _ingested_at TIMESTAMP
)
PARTITION BY snapshot_date;
