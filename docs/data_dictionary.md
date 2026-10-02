# Data dictionary

_Last updated: 1 Oct 2026 · Fill rates measured on 430,549 resources (latest row per `identifier`)._

Project `learning-reach-473210`, BigQuery location `asia-south1`. Table DDL: `infra/bigquery/01_create_tables.sql`.

---

## Source: DIKSHA

DIKSHA is the Government of India's national platform for school content (NCERT, CBSE, state boards). Data comes from its public content search API (`POST https://diksha.gov.in/api/content/v1/search`, no key), loaded by the Cloud Run function `diksha-ingest` (`functions/diksha/main.py`).

**Scope:** live learning resources only (`status = Live`) in 10 categories: Explanation Content, Learning Resource, eTextbook, Practice Question Set, Teacher Resource, Digital Textbook, Course Assessment, Content Playlist, Exam Question, Course. Raw assets (images, audio) and certificate templates are excluded (~487K items).

**API quirks that shape the tables:**
- Empty fields are **omitted** from the response, not sent as null.
- Some fields are lists (one resource can be tagged Classes 1–10), and sometimes a plain string instead of a list.
- A search can only be paged up to 10,000 results, so loads are split into date-range slices.
- Usage numbers are **running totals** (all-time), not per day.

---

## `raw.diksha_content`

One row per resource **per load**: the same resource appears again whenever it is re-fetched (full load, or incremental after an edit). Use the latest row per `identifier` (by `_ingested_at`) for analysis. Partitioned by `DATE(_ingested_at)`, clustered by `identifier`.

### Descriptive fields

| Column | Type | Meaning | Example | Filled | Notes |
|---|---|---|---|---|---|
| `identifier` | STRING | Unique ID of the resource | `do_3126132210160271362631` | 100% | **Primary key.** Never changes. Upserts and dedupes use it. |
| `name` | STRING | Title | `1.2 Video - Sounds of Letters` | 100% | Often has leading spaces, `(NEW)` prefixes, mixed scripts. Trim in staging. |
| `board` | STRING | Education board it is for | `State (Maharashtra)`, `CBSE` | 93.4% | ~44 boards, incl. union territories (`UT (DNH and DD)`). Map to states in staging. |
| `medium` | ARRAY&lt;STRING&gt; | **Language of instruction**: which schools/students it is for | `["Marathi"]` | 94.8% | **Use this as the language field.** An English lesson for Marathi-medium schools has `medium = Marathi`. Needs cleaning: case variants (`PUNJABI`/`Punjabi`), spellings (`Oriya`→`Odia`, `Santhali`→`Santali`, `Sankrit`→`Sanskrit`, `Pharsi`→`Persian`, `Arabi`→`Arabic`, `Konakni`→`Konkani`), non-languages (`Other`, `Languages`). ~150 distinct values, ~100 of them with ≤ 3 resources. Top: English 164.6K, Hindi 107.8K, Kannada 23.5K, Gujarati 22.1K, Marathi 18.9K, Telugu 16.5K, Tamil 15.5K, Odia 13.7K, Urdu 13.3K. |
| `gradeLevel` | ARRAY&lt;STRING&gt; | Classes it is tagged for | `["Class 6", "Class 7"]` | 96.2% | Often many grades on one item (up to Classes 1–10). Normalise `Class 8` → 8 in staging. |
| `subject` | ARRAY&lt;STRING&gt; | Subjects | `["Mathematics"]` | 92.1% | ~450 raw values; mixed case and synonyms (`EVS` / `Environmental Studies`). Map in staging. |
| `language` | ARRAY&lt;STRING&gt; | Content language tag | `["English"]` | 100% | ⚠️ **Do not use.** 423,821 of 430K say `English` (form default), incl. Hindi/Marathi content. |
| `audience` | ARRAY&lt;STRING&gt; | Intended user | `["Student"]`, `["Teacher"]` | ~100% (sample) | |
| `primaryCategory` | STRING | Type of resource | `Explanation Content` | 100% (filter) | Counts: Explanation Content 199.8K, Learning Resource 82.4K, eTextbook 51.0K, Practice Question Set 42.8K, Teacher Resource 28.6K, Digital Textbook 11.5K, Course Assessment 6.2K, Content Playlist 5.5K, Exam Question 2.4K, Course 0.2K. |
| `contentType` | STRING | Older type label | `Resource`, `eTextBook` | ~100% (sample) | Overlaps `primaryCategory`; prefer `primaryCategory`. |
| `mimeType` | STRING | File format | `video/mp4`, `application/pdf`, `video/x-youtube`, `application/vnd.ekstep.ecml-archive` (interactive) | ~100% (sample) | Group into video / PDF / interactive / HTML / collection in staging. |
| `objectType` | STRING | DIKSHA object kind | `Content`, `Collection` | ~100% (sample) | |
| `license` | STRING | Content licence | `CC BY 4.0` | 100% | Record it; many items are Creative Commons. |
| `channel` | STRING | ID of the publishing organisation | `0123…` | ~100% (sample) | An ID, not a name. |
| `framework` | STRING | Curriculum framework ID | `ekstep_ncert_k-12` | ~100% (sample) | |

### Dates

All stored as **STRING** in DIKSHA's format `2018-10-16T14:43:22.143+0000` (UTC). Parse to TIMESTAMP in staging. Strings sort correctly, so `MAX()` works on them as text.

| Column | Meaning | Filled | Notes |
|---|---|---|---|
| `createdOn` | When the resource was first created | ~100% | Earliest Jan 2016. Used to slice full loads. |
| `lastUpdatedOn` | Last edit or republish | ~100% | Earliest Jul 2018. **319K of 430K were last updated in 2023** (a bulk update). Used for incremental loads. |
| `lastPublishedOn` | Last publish | ~100% | |

### Usage numbers (running totals at load time)

| Column | Type | Meaning | Example | Filled | Notes |
|---|---|---|---|---|---|
| `play_sessions_json` | STRING (JSON) | Total times opened/played, per channel | `{"portal": 126133}` | 93.1% | Extract with `JSON_VALUE(play_sessions_json, '$.portal')`. Portal (web) only. |
| `time_spent_json` | STRING (JSON) | Total seconds spent, per channel | `{"portal": 20321162}` | ~93% | Same shape as play sessions. |
| `avg_rating` | FLOAT64 | Average user rating (1–5) | `3.79` | 56.7% | Treat as NULL when `ratings_count` is 0 or NULL. |
| `ratings_count` | INT64 | Number of ratings | `873` | 56.6% | |
| `downloads` | INT64 | Total downloads | `79` | **0.4%** | Almost never present; ignore in analysis. |

For **daily** usage, use `raw.diksha_metrics_snapshot` instead (these columns only reflect the moment the row was loaded).

### Load metadata

| Column | Type | Meaning |
|---|---|---|
| `slice_id` | STRING | Date-range slice the row came from, e.g. `createdOn:20210301T000000_20210408T000000` |
| `_mode` | STRING | `full` or `incremental` |
| `_load_id` | STRING | ID of the run that loaded the row (matches `ops.ingestion_log.run_id`) |
| `_ingested_at` | TIMESTAMP | When the row was loaded |

---

## `raw.diksha_metrics_snapshot`

A daily "photo" of usage for **every** resource: one row per resource per `snapshot_date`. Written by the `metrics` mode (part of the daily 06:00 IST run). Builds the usage history DIKSHA doesn't provide: plays on day X = total on day X − total on day X−1. Partitioned by `snapshot_date`, clustered by `identifier`. ~430K rows/day (~20 MB).

| Column | Type | Meaning |
|---|---|---|
| `snapshot_date` | DATE | Day the photo was taken (UTC date of the run) |
| `identifier` | STRING | Resource ID (joins to `raw.diksha_content.identifier`) |
| `play_sessions_json` | STRING (JSON) | Running total of plays, e.g. `{"portal": 4133}` |
| `time_spent_json` | STRING (JSON) | Running total of seconds spent |
| `avg_rating` | FLOAT64 | Average rating at that moment |
| `ratings_count` | INT64 | Number of ratings at that moment |
| `downloads` | INT64 | Downloads (rarely present) |
| `_load_id` | STRING | Run ID |
| `_ingested_at` | TIMESTAMP | Load time |

⚠️ **To verify (2–3 Oct 2026):** whether DIKSHA refreshes these totals daily. If not, day-to-day differences will be zero.

If a run is repeated on the same day, a resource can have two rows for one `snapshot_date`; keep the latest `_ingested_at`.

---

## `ops.ingestion_log`

The run diary: one row per mode per run, including failures. Partitioned by `DATE(started_at)`.

| Column | Type | Meaning |
|---|---|---|
| `run_id` | STRING | Run ID (= `_load_id` on the rows it loaded) |
| `source` | STRING | `diksha` |
| `mode` | STRING | `full`, `incremental` or `metrics` |
| `started_at` / `finished_at` | TIMESTAMP | Run start / end |
| `status` | STRING | `success`, `failed`, or `partial` (test run with a row limit; never used as a watermark) |
| `rows_loaded` | INT64 | Rows written |
| `slices` | INT64 | Date-range slices processed |
| `watermark` | STRING | Incremental only: the `lastUpdatedOn` the run fetched from |
| `error_message` | STRING | Why it failed (NULL on success) |

The incremental mode reads this table to decide where to start: last successful `full`/`incremental` `started_at` minus 1 day.

---

## Source: World Bank + UNESCO UIS

National education indicators for India and peers (Bangladesh, Pakistan, Nepal, Sri Lanka), loaded monthly by the Cloud Run function `indicators-ingest` (`functions/indicators/main.py`). Both APIs are public, no key. Each run reloads the full history and appends it; use the latest `_load_id` per table. DDL: `infra/bigquery/02_create_indicator_tables.sql`.

Every field the APIs return is stored as a column, and `record_json` keeps the original record untouched.

## `raw.worldbank_indicators`

Indicators: `SE.PRM.ENRR`, `SE.SEC.ENRR` (gross enrolment, primary/secondary), `SE.PRM.CMPT.ZS`, `SE.SEC.CMPT.LO.ZS` (completion, primary/lower secondary), `SE.PRM.UNER` (children out of school, primary), `SE.ADT.LITR.ZS` (adult literacy), `SE.PRM.ENRL.TC.ZS` (pupil-teacher ratio, primary), `SE.XPD.TOTL.GD.ZS` (govt education spending, % of GDP). ~2,640 rows per load.

| Column | Type | Meaning | Example |
|---|---|---|---|
| `indicator_id` | STRING | Indicator code | `SE.PRM.ENRR` |
| `indicator_name` | STRING | Indicator name | `School enrollment, primary (% gross)` |
| `country_id` | STRING | 2-letter country code | `IN` |
| `country_name` | STRING | Country name | `India` |
| `country_iso3` | STRING | 3-letter country code | `IND` |
| `year` | INT64 | Year | `2025` |
| `value` | FLOAT64 | The figure. **NULL when that year has no data** (common). Gross ratios can exceed 100 (India primary = 111). | `111.03` |
| `unit` | STRING | Unit (usually empty → NULL) | |
| `obs_status` | STRING | Observation flag (usually empty → NULL) | |
| `decimal` | INT64 | Suggested display decimals | `0` |
| `source_last_updated` | STRING | When the World Bank last updated the indicator | `2026-07-13` |
| `record_json` | STRING | Original API record | |
| `_load_id`, `_ingested_at` | | Run ID, load time | |

## `raw.uis_indicators`

Indicators: `CR.1`, `CR.2` (completion rate, primary/lower secondary), `ROFST.1.CP` (out-of-school rate, primary). ~150 rows per load.

| Column | Type | Meaning | Example |
|---|---|---|---|
| `indicator_id` | STRING | Indicator code | `CR.1` |
| `indicator_name` | STRING | Indicator name (from metadata) | `Completion rate, primary education, both sexes (%)` |
| `geo_unit` | STRING | Country code (3-letter) | `IND` |
| `year` | INT64 | Year | `2019` |
| `value` | FLOAT64 | The figure | `94.15` |
| `magnitude` | STRING | Scale note (usually NULL) | |
| `qualifier` | STRING | Data-quality note, e.g. UIS estimate (usually NULL) | |
| `indicator_theme` | STRING | Theme | `EDUCATION` |
| `last_data_update` | STRING | Date of UIS's last data release (MM/DD/YYYY) | `02/09/2026` |
| `last_data_update_description` | STRING | Release name | `February 2026 Data Release` |
| `indicator_metadata_json` | STRING | Full indicator metadata incl. glossary terms | |
| `record_json` | STRING | Original API record | |
| `_load_id`, `_ingested_at` | | Run ID, load time | |

---

## Source: YouTube Data API v3

Public stats of the Khan Academy India channels (`UCU0kWLAbhVGxXarmE3b8rHg`, 1,869 videos; `UCg4BkaHyyE_4-RvEMJ2PTtA` English, 3,889 videos on 2 Oct 2026), loaded by the Cloud Run function `youtube-ingest` (`functions/youtube/main.py`) using an API key from Secret Manager. Not affiliated with the channels; public data only. Timestamps are ISO 8601 STRINGs (e.g. `2026-09-29T10:00:00Z`). DDL: `infra/bigquery/03_create_youtube_tables.sql`.

Not available for other people's channels: dislikes, watch time, audience demographics, traffic sources (owner-only YouTube Analytics).

## `raw.youtube_videos`

One row per video, written the first time the video is seen (all videos again if run with `refresh_details=true`). Counts here are as of that moment; use the stats snapshot for trends.

| Column | Type | Meaning |
|---|---|---|
| `video_id` | STRING | Video ID (key) |
| `etag` | STRING | Version tag of YouTube's copy |
| `channel_id`, `channel_title` | STRING | Channel |
| `published_at` | STRING | Upload time |
| `title` | STRING | Title; tags like `[Hinglish]`, `Grade 12`, subject can be parsed |
| `description` | STRING | Description; often links to the practice exercise (course/unit/lesson) |
| `tags` | ARRAY&lt;STRING&gt; | Uploader's tags |
| `category_id` | STRING | YouTube category (27 = Education) |
| `default_language`, `default_audio_language` | STRING | Metadata / audio language (e.g. `hi`, `en`) |
| `live_broadcast_content` | STRING | `none` for normal uploads |
| `duration` / `duration_seconds` | STRING / INT64 | Length (`PT12M34S` / 754) |
| `dimension`, `definition`, `projection` | STRING | 2d/3d, hd/sd, rectangular/360 |
| `caption` | BOOL | Has captions |
| `licensed_content` | BOOL | Claimed content |
| `content_rating_json`, `region_restriction_json` | STRING | Ratings / blocked regions, if any |
| `upload_status`, `privacy_status` | STRING | e.g. `processed`, `public` |
| `license` | STRING | `youtube` or `creativeCommon` |
| `embeddable`, `public_stats_viewable`, `made_for_kids` | BOOL | Flags |
| `topic_categories` | ARRAY&lt;STRING&gt; | Wikipedia topic URLs |
| `localizations_json` | STRING | Translated titles/descriptions per language |
| `recording_date` | STRING | Rarely filled |
| `view_count`, `like_count`, `comment_count` | INT64 | Totals when written (`like_count` NULL if hidden) |
| `record_json` | STRING | Original API resource |
| `_load_id`, `_ingested_at` | | Run ID, load time |

## `raw.youtube_video_stats_snapshot`

Daily photo of every public video's counters. Views on day X = `view_count`(X) − `view_count`(X−1). History starts from the first run (2 Oct 2026).

| Column | Type | Meaning |
|---|---|---|
| `snapshot_date` | DATE | Day of the run (UTC) |
| `video_id`, `channel_id` | STRING | Keys |
| `view_count` | INT64 | Total views |
| `like_count` | INT64 | Total likes (NULL if hidden) |
| `comment_count` | INT64 | Total comments (NULL if comments off) |
| `_load_id`, `_ingested_at` | | Run ID, load time |

## `raw.youtube_channel_snapshot`

Daily photo of each channel: `snapshot_date`, `channel_id`, `title`, `description`, `custom_url` (@handle), `published_at`, `country`, `default_language`, `view_count` (all videos), `subscriber_count` (rounded by YouTube to 3 significant figures), `hidden_subscriber_count`, `video_count`, `uploads_playlist_id`, `keywords`, `topic_categories`, `privacy_status`, `made_for_kids`, `record_json`, `_load_id`, `_ingested_at`.

---

## Source: Google Sheet "Localization Plan" (SYNTHETIC)

A hand-maintained planning sheet that simulates an EdTech content team's plan: which language × board × grade × subject they will create content for next. Created from `reference/localization_plan_seed.csv` (25 rows, with 10 deliberate data-quality problems listed in `reference/localization_plan_seed.md`). Sheet ID `1L6jXt19Rl7xMNGVa-5WIWfZNnfluikVIng10VyTe2H0`, tab `plan`, shared read-only with `ingestion-sa`. Loaded by the Cloud Run function `sheets-ingest` (`functions/sheets/main.py`). DDL: `infra/bigquery/04_create_sheet_table.sql`.

## `raw.localization_plan_snapshot`

A full copy of the sheet per run (append-only), so edits build a history. Every sheet value is stored **as text, exactly as typed**: nothing trimmed or converted. Blank cells are NULL. Use the latest `_load_id` for the current plan.

| Column | Meaning | Clean values | Known mess in the seed |
|---|---|---|---|
| `plan_id` | Plan row ID | `LP-001`…`LP-024` | LP-005 appears twice (duplicate row) |
| `language` | Language to create content in (joins to DIKSHA `medium`) | Hindi, Marathi, Gujarati, Kannada, Punjabi, Assamese, Odia, English | `"Marathi "`, `marathi`, `MR`, `Oriya` |
| `board` | Education board (joins to DIKSHA `board`, case-insensitive) | CBSE, State (Maharashtra/Gujarat/Karnataka/Punjab/Assam/Uttar Pradesh/Odisha) | |
| `grade` | Class | `Class 3`…`Class 10` | `6`, `VII` |
| `subject` | Subject (joins to DIKSHA `subject`) | Mathematics, Science, English, Social Science, Environmental Studies, Hindi | |
| `resources_planned` | Number of resources planned | whole number 10–120 | `forty` |
| `owner` | Responsible team (no personal data) | content-north/west/south/east, translation-team | |
| `status` | Plan status | planned, in_production, published, dropped | one blank |
| `target_date` | Planned completion | `YYYY-MM-DD` | `15/12/2026` |
| `last_updated` | Last edit date | `YYYY-MM-DD` | |
| `notes` | Free text | | |
| `_row_number` | Row number in the sheet (header = 1) | | |
| `row_json` | Full row keyed by header, incl. any extra columns | | |
| `_sheet_id`, `_load_id`, `_ingested_at` | Sheet ID, run ID, load time | | |
