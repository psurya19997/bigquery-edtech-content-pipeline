# Ingestion design & test log

_How each source was investigated before writing code, what problems the APIs have, and how the ingestion code handles each one. Dates are 29 Sep – 2 Oct 2026._

Related docs: `PROGRESS.md` (status), `docs/data_dictionary.md` (table columns).

---

## Contents

1. [Common design (all sources)](#1-common-design-all-sources)
2. [data.gov.in / UDISE+: tested and dropped](#2-datagovin--udise-tested-and-dropped)
3. [DIKSHA](#3-diksha)
4. [World Bank](#4-world-bank)
5. [UNESCO UIS](#5-unesco-uis)
6. [YouTube](#6-youtube)
7. [Google Sheet "Localization Plan"](#7-google-sheet-localization-plan)
8. [Deployment issues hit and fixed](#8-deployment-issues-hit-and-fixed)
9. [Summary table](#9-summary-table)

---

## 1. Common design (all sources)

Every source follows the same pattern, so a failure in one never affects another and every run is traceable.

| Concern | Design | Why |
|---|---|---|
| Where code runs | One Cloud Run function per source, region `asia-south1` (Mumbai), runs as service account `ingestion-sa` | Indian IP for Indian government APIs; isolated failures; least privilege |
| Raw storage | **Append-only** raw tables; every row has `_load_id` (run ID) and `_ingested_at` | Reruns never destroy data; history of every load; staging picks the latest row |
| Writing to BigQuery | Batch **load jobs** (`load_table_from_json`, `WRITE_APPEND`) using the table's own schema | Load jobs are free (streaming inserts are not); explicit schema stops type drift |
| Retries | `tenacity`: exponential backoff on HTTP 429/5xx, connection errors and timeouts, max 6 attempts | All tested APIs fail intermittently |
| Not retried | 4xx errors (400, 403, 404) | Retrying returns the same answer; fail fast with a clear message |
| Run diary | One row per run in `ops.ingestion_log` (`status` = success / failed / partial, `rows_loaded`, `error_message`) — written even when the run fails | Failures are visible in BigQuery, not hidden in logs |
| HTTP reply | 200 if all parts succeeded, 500 if any failed | Cloud Scheduler marks the job failed, so it shows in the console |
| Concurrency | Cloud Run max instances = 1, max concurrent requests = 1 | Two runs can never overlap and double-load |
| Scheduler retries | 0 | A failed run is logged; the next run recovers (see each source) — no accidental double loads |
| Local testing | Each function has `--dry-run` (fetch, write nothing) | Test against live APIs without touching BigQuery |
| Unit tests | `tests/test_*.py` with fake API responses, no network (33 tests, all passing) | Repeatable; run with `python -m pytest -q` |

**Important environment fact (tested 29 Sep):** Cloud Shell runs outside India (`curl ipinfo.io/country` → `TW`, Taiwan). Indian government APIs may drop that traffic, so API probes were run from the local PC (India) and the functions run in Mumbai.

---

## 2. data.gov.in / UDISE+: tested and dropped

**Planned use (v1):** district-level school enrolment from UDISE+ via `https://api.data.gov.in/resource/{resource_id}?api-key=…&format=json`.

### Tests

| # | Test | Where from | Result |
|---|---|---|---|
| 1 | Resource request with real key | Cloud Shell | Empty output; with errors visible: HTTP 503 page from nginx |
| 2 | `curl -i` with retries (8 tries) | Cloud Shell | `HTTP 000` (no connection, 30 s timeout) every time |
| 3 | `curl ipinfo.io/country` | Cloud Shell | `TW` → Cloud Shell is in Taiwan |
| 4 | Endpoint status without key, repeated | Local PC (India) | Mix of 503 and `400 "Authorization field missing"` (= server up) |
| 5 | Specific resource, no key vs fake key, 6 tries | Local PC | 400 / 403 on 5 of 6, 503 on 1 of 6 |
| 6 | Specific resource with real key | Local PC | 503 |
| 7 | Earlier research (brief) | — | 503, 500 "problem proxying the request", 60 s timeouts |

### Findings
- The API front door works but the data backend frequently returns 503.
- Non-Indian IPs are dropped (Cloud Shell timeouts).
- One resource per district per year → thousands of calls for any real coverage.

### Decision
Dropped (decision record #1 in `PROGRESS.md`). A scheduled pipeline on this source would mostly log failures. Replaced by DIKSHA, which needs no key and was reliable in every test. The unused secret `datagovin-api-key` can be deleted.

---

## 3. DIKSHA

**API:** `POST https://diksha.gov.in/api/content/v1/search`, no key, JSON body `{"request": {"filters", "fields", "limit", "offset", "sort_by", "facets"}}`.
**Code:** `functions/diksha/main.py` · **Tables:** `raw.diksha_content`, `raw.diksha_metrics_snapshot` · **Schedule:** daily 06:00 IST (`diksha-daily`, body `{"mode": "daily"}`).

### 3.1 Tests run while designing the query

| # | Question | Test | Result | Design consequence |
|---|---|---|---|---|
| 1 | Does it work from India? | `scripts/check_apis.py` | HTTP 200, 2,298 Marathi Class 6 items | Usable |
| 2 | How big is the corpus? | `limit: 0` count, `status: Live` | **917,607** items | Far bigger than the brief's 275K |
| 3 | What is in it? | Facets on `primaryCategory`, `contentType`, `objectType`, `mimeType` | asset 382,393; certificate template 104,556; explanation content 199,837; …; `contentType = resource` 275,216 (the brief's number) | Filter to 10 **learning** categories → **430,435** items |
| 4 | Does `objectType` filter work? | Count with `objectType: ["Content"]` | Same 917,607 → filter is **ignored** | Don't rely on it; filter by `primaryCategory` |
| 5 | Is the category filter case-sensitive? | Same filter in lowercase | Same 430,435 | Either case works |
| 6 | Paging limit? | `offset` 9,990 vs 10,000 | 9,990 → 200, **10,000 → HTTP 500** | Hard cap: a search can only reach 10,000 results |
| 7 | Can a date range split the corpus cleanly? | `createdOn < 2019` + `createdOn >= 2019` | 287,708 + 629,899 = **917,607 exactly** | Date ranges partition the data with no overlap and no gaps |
| 8 | Which date formats work? | `"2019-01-01"` vs `"2019-01-01T00:00:00.000+0000"` | Both → 112,563 for 2019 | Use DIKSHA's own full format |
| 9 | Earliest item? | `sort_by: createdOn asc`, limit 2 | 2016-01-24 | Start full loads at 2015-01-01 |
| 10 | Are pages stable? | One week (1,468 items), pages 0 and 1000, fetched twice, `sort_by identifier asc` | No overlap, identical on re-fetch, sorted | Always sort by `identifier` when paging |
| 11 | Which fields exist, in what shape? | 1,000 items, all fields requested | Missing fields are **omitted**; `gradeLevel`, `medium`, `subject` are arrays (sometimes a string); play/time are nested `{"portal": n}`; `avg_rating` int or float; in this (oldest) sample `medium` only 95/1000, `board` 22/1000 | Normalise every field; explicit schema; arrays as `ARRAY<STRING>` |
| 12 | `lastUpdatedOn` range for incremental? | Sort asc/desc + count per year | Oldest 2018-07-06; 2016–2022: 83 items in total; **2023: 319,394** (bulk update); 2024–26 ~35–39K/yr | Incremental by `lastUpdatedOn`; bisect handles the 2023 spike |
| 13 | How much changes per day? | Incremental dry run `--since 2026-09-29` | 149 items | Daily run is small |
| 14 | Does slicing hold on real data? | Planner on Assamese only | 5,807 items, 1 slice | Planner works end to end |
| 15 | Fill rates on the full load (BigQuery) | `COUNTIF` per column on 430,549 rows | medium 94.8%, grade 96.2%, board 93.4%, subject 92.1%, plays 93.1%, rating 56.7%, downloads 0.4% | The test-11 sample was biased (oldest items); full data is fine |
| 16 | Which field means "language"? | Value counts of `medium` vs `language` | `language` = "English" on 423,821 items; `medium` has real values but case/spelling variants | Use `medium`; normalise in staging |
| 17 | Do usage totals change daily? | Sum of plays per `snapshot_date` | 1 Oct 2,530,650,560 → 2 Oct 2,530,690,820 (**+40,260**) | Daily snapshot is worth keeping |

### 3.2 API issues and how the code handles them

| Issue | Evidence | Handling in code |
|---|---|---|
| Offset ≥ 10,000 → HTTP 500 | Test 6 | `iter_slices` bisects a date range until each slice has ≤ **9,000** items (1,000 headroom for items published mid-run). `search()` refuses `offset + limit > 10,000`. |
| Huge corpus | Test 2–3 | Category allowlist (`LEARNING_CATEGORIES`) |
| Board/medium/grade are arrays or missing | Test 11 | Slicing uses **dates** (single-valued, always present), never those fields — avoids double-counting and silent drops |
| A 1-second burst > 9,000 items | Theoretical | Slice flagged `over_cap`, logged as a warning (never seen in practice) |
| Page order could shuffle | Test 10 | `sort_by: {"identifier": "asc"}` on every page |
| Intermittent 5xx | Seen during loads | Retry 6×, wait 2 → 60 s, 60 s timeout |
| Missing fields omitted | Test 11 | `_as_str` / `_as_list` → NULL or `[]`; explicit table schema |
| String-or-array fields | Test 11 | `_as_list` wraps a string into a list |
| Nested usage numbers | Test 11 | Stored as JSON text (`play_sessions_json`), extracted in staging |
| User-ID fields in responses | API returns `createdBy` etc. if asked | Only an explicit field list is requested, so they never arrive |
| Politeness | Public government API | ≤ 2 requests/s (`MIN_INTERVAL = 0.5`), identifying `User-Agent` |
| `language` field useless | Test 16 | Documented; staging uses `medium` |

### 3.3 How the query works (per mode)

| Mode | Filter | Slices by | Fields | Writes to |
|---|---|---|---|---|
| `full` | `status = Live`, 10 categories | `createdOn` from 2015-01-01 to tomorrow | All content fields | `raw.diksha_content` |
| `incremental` | same + `lastUpdatedOn ≥ watermark` | `lastUpdatedOn` from watermark to tomorrow | All content fields | `raw.diksha_content` |
| `metrics` | same | `createdOn` | `identifier` + 5 usage fields | `raw.diksha_metrics_snapshot` |
| `daily` | `incremental` then `metrics` | | | both |

Per slice: page through 1,000 at a time (offset 0 → 9,000), sorted by identifier → normalise rows → one load job per slice.

**Watermark (incremental):** `MAX(started_at)` of the last **successful** `full`/`incremental` run in `ops.ingestion_log`, **minus 1 day** overlap. Failed runs and `partial` test runs (`limit` set) are ignored, so a missed or test day can never create a gap. If there is no successful run, incremental stops with `"run mode=full first"` (it refuses to guess). Overlap duplicates are removed in staging (latest row per `identifier`).

### 3.4 Results
- Full load: ~430K resources. Metrics: **430,549 rows, 72 slices, 9 minutes**.
- Incremental: a few hundred rows per day.
- Unit tests (12): slices fit the cap and cover every item once; slices are ordered and non-overlapping; empty corpus; one-count shortcut; 1-second burst flagged; base filters passed through; offset guard; sparse-item normalisation; metrics row shape; full run loads everything and logs success; limited run logged as `partial`; failures logged, not raised.

### 3.5 Known limitation
Resources that DIKSHA deletes or unpublishes are not detected (no weekly full reload, by choice). A manual `{"mode": "full"}` run refreshes everything if needed.

---

## 4. World Bank

**API:** `GET https://api.worldbank.org/v2/country/IND;BGD;PAK;NPL;LKA/indicator/{id}?format=json&per_page=1000&page=N`, no key.
**Code:** `functions/indicators/main.py` · **Table:** `raw.worldbank_indicators` · **Schedule:** manual (data changes ~yearly).

### Tests

| # | Test | Result | Design consequence |
|---|---|---|---|
| 1 | Smoke test (3 countries, 1 indicator) | 200, 198 records | Usable |
| 2 | Response shape | `[page_meta, [records]]`; record = indicator{id,value}, country{id,value}, countryiso3code, date, value, unit, obs_status, decimal | Flatten every field; keep original as `record_json` |
| 3 | Five countries in one call (`IND;BGD;PAK;NPL;LKA`) | 330 records, `pages: 1` | One call per indicator; still follow `pages` |
| 4 | Reliability from the PC | Two 60 s **timeouts**, then 0.3 s; a later probe: 7.2 s, then 0.3 s ×4 | Short timeout (30 s) + retries beats one long wait |
| 5 | Full dry run from the PC | 20 timeouts retried, **2,640 rows in 12 min** — succeeded | Retry logic proven |
| 6 | Run from Cloud Run (Mumbai) | **2,640 rows in 4 s**, no timeouts | The slowness was the PC's network path, not the API |
| 7 | Data quirks | Missing years have `value: null`; gross enrolment can exceed 100 (India primary 111) | Keep NULLs; document; flag gross ratios in staging |

### Issues and handling
| Issue | Handling |
|---|---|
| Intermittent hangs | 30 s timeout, retry 6×, wait 2 → 30 s |
| Error replies are a 1-element list (`[{"message": …}]`) instead of `[meta, data]` | Detected and raised as a clear `ValueError` (not retried) |
| Multiple pages possible | Loop until `page >= pages` |
| Null values | Stored as NULL, not dropped |
| Update date only in page header | Stored per row as `source_last_updated` |

**Query design:** 8 indicators × 5 countries, full history every run, appended with a new `_load_id` (staging keeps the latest load). Unit tests: null years, every field + original JSON kept, paging across indicators, error message raises, run logs success, failures logged.

---

## 5. UNESCO UIS

**API:** `GET https://api.uis.unesco.org/api/public/data/indicators?indicator=…&geoUnit=…&indicatorMetadata=true`, no key.
**Code:** `functions/indicators/main.py` (same function as World Bank) · **Table:** `raw.uis_indicators` · **Schedule:** manual.

### Tests
| # | Test | Result | Design consequence |
|---|---|---|---|
| 1 | Smoke test (3 indicators × 3 countries) | 200, 87 records | Usable |
| 2 | 5 countries + `indicatorMetadata=true` | 200, keys `hints`, `records`, `indicatorMetadata`; **152 records** | One call gets everything |
| 3 | Repeated query keys (`indicator=CR.1&indicator=CR.2…`) | Accepted | Pass lists as params |
| 4 | Metadata content | Name, theme, `lastDataUpdate` 02/09/2026, "February 2026 Data Release", glossary terms | Store name, theme, release; full metadata as JSON |
| 5 | Run from Cloud Run | 152 rows in ~9 s | Works |

### Issues and handling
| Issue | Handling |
|---|---|
| Indicator names only in metadata | Joined in by `indicatorCode` |
| Nullable `magnitude` / `qualifier` | Stored as-is |
| Same retry needs as World Bank | Shared `get_json` with retries |
| One source failing | Each source runs and logs separately (`run_source`), so World Bank failing doesn't stop UIS |

---

## 6. YouTube

**API:** YouTube Data API v3 (`https://www.googleapis.com/youtube/v3/…`), API key.
**Code:** `functions/youtube/main.py` · **Tables:** `raw.youtube_videos`, `raw.youtube_video_stats_snapshot`, `raw.youtube_channel_snapshot` · **Schedule:** daily 06:30 IST (`youtube-daily`, body `{}`).

### 6.1 RSS feed: tested and replaced
| Test | Result |
|---|---|
| `check_apis.py` | `khan_india`: HTTP 500 |
| 3 rounds × 2 channels | 500, 404, 404, 200, 500 … (mixed) |
| Feed content | Latest 15 videos only |

Not blocked, just unreliable and shallow → replaced by the official Data API.

### 6.2 Data API design
| Step | Call | Cost |
|---|---|---|
| 1 | `channels.list` (snippet, statistics, contentDetails, brandingSettings, topicDetails, status) | 1 unit |
| 2 | `playlistItems.list` on each channel's **uploads playlist**, 50 per page, follow `nextPageToken` | 1 unit/page |
| 3 | `videos.list`, 50 IDs per call, all useful parts (cost is the same for any number of parts) | 1 unit/call |

Writes: channel totals (daily), counters of every video (daily), full details only for videos not seen before (`refresh_details=true` rewrites all).

### 6.3 Issues and handling
| Issue | How it showed up | Handling |
|---|---|---|
| Key must not leak | — | Key restricted to YouTube Data API v3; stored in Secret Manager (`youtube-api-key`); exposed to the function as env var `YOUTUBE_API_KEY`; error messages use YouTube's text and **never log the URL** (the key is in it) |
| Quota exhausted (403) | — | Not retried (retrying wastes nothing but helps nothing); clear message logged |
| 5xx / timeouts | — | Retried 6×, 2 → 30 s |
| Hidden likes / comments off | — | `like_count` / `comment_count` NULL |
| Duration as ISO 8601 | `PT12M34S` | Parsed to `duration_seconds` (handles days/hours/minutes/seconds) |
| `caption` sent as the string "true" | — | Converted to BOOL |
| Private/deleted videos in the playlist | — | `videos.list` omits them; counted as uploads vs public in the log |
| **Out of memory** | First run: `Memory limit of 1024 MiB exceeded with 1154 MiB used` → bare "Service Unavailable" | Holding all 5,758 video resources at once was too much. Now processes **50 videos at a time** and saves details in **chunks of 500** — memory stays flat |
| View history can't be backfilled | YouTube shows only today's totals | Daily snapshot from day 1 (2 Oct 2026) |

### 6.4 Results
5,758 videos (1,869 + 3,889) in **93 s**, ~230 quota units of 10,000/day. Unit tests (8): duration parsing; flattening + original JSON; paging uploads and batching videos; only new videos get details; details flushed in chunks; refresh rewrites all; missing key logged; 403 surfaces without retry.

---

## 7. Google Sheet "Localization Plan"

**API:** Google Sheets API v4 `GET /v4/spreadsheets/{id}/values/plan?valueRenderOption=FORMATTED_VALUE`, authenticated as the Cloud Run service account (no key).
**Code:** `functions/sheets/main.py` · **Table:** `raw.localization_plan_snapshot` · **Schedule:** manual.

### Preparation and checks
| Check | Result |
|---|---|
| Seed file `reference/localization_plan_seed.csv` (25 rows, SYNTHETIC) | Verified by reading the file: all 10 planted problems present (trailing space, lowercase, code, old spelling, bare grade, Roman grade, DD/MM date, blank status, word instead of number, duplicate row) |
| Do the join columns exist in DIKSHA? | Live DIKSHA facets: all 8 boards (9.4K–33.3K items each) and all 6 subjects (9.0K–87.4K) exist |
| Import into Sheets | With **"Convert text to numbers, dates and formulas" OFF**, otherwise Sheets silently "fixes" the planted mess |
| Link given first was a Drive file (`drive.google.com/file/d/…`) | A raw CSV in Drive is not readable by the Sheets API; created a real Sheet (`docs.google.com/spreadsheets/d/…`) instead |

### Issues and handling
| Issue | Handling |
|---|---|
| Sheet not shared with the service account | HTTP 403 → message "share the sheet with ingestion-sa… (Viewer)" |
| Wrong ID or tab name | HTTP 404 → message naming the sheet ID and tab |
| Someone renames/deletes a column | Header checked against the 11 expected columns → fails loudly instead of loading junk |
| API drops trailing empty cells | Short rows padded with NULL |
| Empty rows | Skipped |
| Mess must reach staging intact | Values read as displayed (`FORMATTED_VALUE`), stored as **text**, nothing trimmed |
| Extra columns added later | Kept in `row_json` |

**Result:** 25 rows in 5 s. Unit tests (6) run the real seed CSV through the loader and check every planted problem survives as typed.

---

## 8. Deployment issues hit and fixed

| Symptom | Cause | Fix / lesson |
|---|---|---|
| BigQuery `GRANT` → "Not found: Dataset" | Query ran in the US location | Set query location to `asia-south1` |
| SQL typed in Cloud Shell → "command not found" | SQL belongs in the BigQuery editor | Use BigQuery → + SQL query (or `bq query`) |
| New Cloud Shell → "project not set" | Session lost config | `gcloud config set project learning-reach-473210` |
| DIKSHA full load would stop at 5 min | Cloud Run default request timeout 300 s | Timeout 3600 s |
| Full load "never ran" | Test-panel request didn't reach the function (no log row) | Always confirm in `ops.ingestion_log` |
| Scheduler 500 on first `daily` | Incremental found no successful full run (by design) | Run `full` once first |
| `youtube-ingest` would not find the key | Env var **Name** was `youtube-api-key` | Name = what code reads (`YOUTUBE_API_KEY`); Secret = `youtube-api-key` |
| YouTube "Service Unavailable" | Out of memory (see 6.3) | Batch processing |
| `youtube-daily` → 403 PERMISSION_DENIED | `ingestion-sa` not Cloud Run Invoker on `youtube-ingest` (the App Engine default account was listed instead) | Add Invoker per service; verify with `gcloud run services get-iam-policy`; allow a few minutes to propagate |
| Logs "No data found" | Logs Explorer defaulted to last 5 minutes | Widen the time range |
| DIKSHA metrics doubled on 1 Oct | Metrics ran twice that day | Expected; staging keeps the latest per `(snapshot_date, identifier)` |

---

## 9. Summary table

| Source | Auth | Main API issue | Key handling | Load style | Schedule | First result |
|---|---|---|---|---|---|---|
| data.gov.in | Key | 503s, blocks non-Indian IPs | — | — | Dropped | — |
| DIKSHA | None | 10K offset cap, sparse/array fields | Date-range bisection, sorted paging, normalisation, watermark | Full once; incremental + metrics daily | Daily 06:00 | 430,549 rows |
| World Bank | None | Intermittent hangs | 30 s timeout + retries | Full reload, append | Manual | 2,640 rows |
| UNESCO UIS | None | Names only in metadata | Metadata join | Full reload, append | Manual | 152 rows |
| YouTube RSS | None | 500/404, 15 videos max | — | — | Replaced | — |
| YouTube Data API | Key (Secret Manager) | Memory with 5.7K videos; quota | Batches of 50, chunks of 500; 403 not retried | Snapshot daily; details once | Daily 06:30 | 5,758 videos |
| Google Sheet | Service account (shared as Viewer) | Human mess, schema edits | Header check, text-only, padding | Full snapshot, append | Manual | 25 rows |
