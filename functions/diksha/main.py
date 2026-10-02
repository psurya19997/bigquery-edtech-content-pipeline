"""DIKSHA -> BigQuery ingestion, deployed as a Cloud Run function (entry point: `run`).

Modes (?mode=... in the URL, or {"mode": ...} as JSON body):
  full         every live learning resource           -> raw.diksha_content  (once, then weekly)
  incremental  resources new/changed since last run   -> raw.diksha_content
  metrics      today's usage numbers for every item   -> raw.diksha_metrics_snapshot
  daily        incremental + metrics (what Cloud Scheduler calls; the default)
Options:
  limit=N       stop after N rows (for testing; logged as "partial", never used as a watermark)
  dry_run=true  fetch from DIKSHA but write nothing to BigQuery
  since=YYYY-MM-DD  incremental start date, overriding the last-run watermark

Run locally (no BigQuery needed with --dry-run):
  python functions/diksha/main.py --mode full --limit 50 --dry-run

Why slicing: DIKSHA returns HTTP 500 for offset >= 10,000, so a search matching more items
can't be paged to the end. We bisect a timestamp range (createdOn for full/metrics,
lastUpdatedOn for incremental) until each slice has <= 9,000 items. Timestamps are
single-valued and always present, so slices never overlap and never miss items.
"""

import argparse
import json
import logging
import os
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import requests
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("diksha")

SEARCH_URL = "https://diksha.gov.in/api/content/v1/search"
USER_AGENT = "learning-content-pulse/0.1 (public-data portfolio project)"
PAGE_SIZE = 1000
OFFSET_CAP = 10_000  # verified 30 Sep 2026: offset 9990 ok, 10000 -> HTTP 500
MAX_SLICE_SIZE = 9_000  # headroom for items published between planning and paging
MIN_INTERVAL = 0.5  # seconds between calls: <= 2 requests/s
FULL_START = datetime(2015, 1, 1, tzinfo=UTC)  # earliest live item is Jan 2016
INCREMENTAL_OVERLAP = timedelta(days=1)  # re-fetch a day back; staging dedupes by identifier
BQ_LOCATION = os.environ.get("BQ_LOCATION", "asia-south1")

# Learning content only: excludes raw assets (images/audio) and certificate templates.
LEARNING_CATEGORIES = [
    "Explanation Content",
    "Learning Resource",
    "eTextbook",
    "Practice Question Set",
    "Teacher Resource",
    "Digital Textbook",
    "Course Assessment",
    "Content Playlist",
    "Exam Question",
    "Course",
]
BASE_FILTERS = {"status": ["Live"], "primaryCategory": LEARNING_CATEGORIES}

ARRAY_FIELDS = ("medium", "gradeLevel", "subject", "language", "audience")
STRING_FIELDS = (
    "identifier", "name", "board", "primaryCategory", "contentType", "mimeType", "objectType",
    "license", "channel", "framework", "createdOn", "lastUpdatedOn", "lastPublishedOn",
)  # fmt: skip
METRIC_FIELDS = (
    "me_totalPlaySessionCount", "me_totalTimeSpentInSec", "me_averageRating",
    "me_totalRatingsCount", "me_totalDownloads",
)  # fmt: skip
# Only these are requested, so user-ID fields (createdBy, lastPublishedBy...) never arrive.
CONTENT_FIELDS = [*STRING_FIELDS, *ARRAY_FIELDS, *METRIC_FIELDS]
METRICS_ONLY_FIELDS = ["identifier", *METRIC_FIELDS]

MODES = ("full", "incremental", "metrics")


# ------------------------------------------------------------------ DIKSHA API
class RetryableHTTPError(requests.HTTPError):
    """Rate limited or server-side failure: worth retrying."""


@retry(
    retry=retry_if_exception_type((RetryableHTTPError, requests.ConnectionError, requests.Timeout)),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    stop=stop_after_attempt(6),
    before_sleep=before_sleep_log(log, logging.WARNING),
    reraise=True,
)
def _post(session: requests.Session, body: dict) -> dict:
    resp = session.post(SEARCH_URL, json=body, timeout=60)
    if resp.status_code in {429, 500, 502, 503, 504}:
        raise RetryableHTTPError(f"HTTP {resp.status_code} from DIKSHA", response=resp)
    resp.raise_for_status()
    return resp.json()


class Diksha:
    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self._last_call = 0.0

    def search(self, filters, *, limit=0, offset=0, fields=None, sort_by=None) -> dict:
        if offset + limit > OFFSET_CAP:
            raise ValueError(f"offset {offset} + limit {limit} exceeds DIKSHA cap {OFFSET_CAP}")
        request = {"filters": filters, "limit": limit, "offset": offset}
        if fields:
            request["fields"] = list(fields)
        if sort_by:
            request["sort_by"] = sort_by
        wait = MIN_INTERVAL - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        try:
            return _post(self.session, {"request": request})["result"]
        finally:
            self._last_call = time.monotonic()

    def count(self, filters: dict) -> int:
        return self.search(filters)["count"]


def fmt_ts(dt: datetime) -> str:
    """UTC datetime in DIKSHA's own format, e.g. 2018-10-16T14:43:22.000+0000."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000+0000")


# ------------------------------------------------------------------ slicing
@dataclass(frozen=True)
class Slice:
    slice_id: str
    filters: dict
    expected_count: int
    over_cap: bool  # even a 1-second range exceeds the cap: some items will be missed


def iter_slices(
    count_fn: Callable[[dict], int],
    base_filters: dict,
    field: str,
    start: datetime,
    end: datetime,
    max_size: int = MAX_SLICE_SIZE,
) -> Iterator[Slice]:
    """Yield non-overlapping [start, end) slices on `field`, oldest first, each <= max_size."""
    stack = [(start, end)]
    while stack:
        lo, hi = stack.pop()
        filters = {**base_filters, field: {">=": fmt_ts(lo), "<": fmt_ts(hi)}}
        n = count_fn(filters)
        if n == 0:
            continue
        span = int((hi - lo).total_seconds())
        if n <= max_size or span <= 1:
            slice_id = f"{field}:{lo:%Y%m%dT%H%M%S}_{hi:%Y%m%dT%H%M%S}"
            yield Slice(slice_id, filters, n, over_cap=n > max_size)
            continue
        mid = lo + timedelta(seconds=span // 2)  # whole seconds, matching fmt_ts
        stack.append((mid, hi))
        stack.append((lo, mid))  # popped first, so output stays oldest-first


def fetch_slice(diksha: Diksha, slc: Slice, fields: list[str]) -> list[dict]:
    items: list[dict] = []
    for offset in range(0, OFFSET_CAP, PAGE_SIZE):
        result = diksha.search(
            slc.filters,
            limit=PAGE_SIZE,
            offset=offset,
            fields=fields,
            sort_by={"identifier": "asc"},  # stable order, so pages don't shuffle
        )
        page = result.get("content") or []
        items.extend(page)
        if len(page) < PAGE_SIZE:
            break
    return items


# ------------------------------------------------------------------ rows
def _as_list(v) -> list[str]:
    if v is None:
        return []
    return [str(x) for x in v] if isinstance(v, list) else [str(v)]


def _as_str(v) -> str | None:
    if v is None:
        return None
    return ", ".join(map(str, v)) if isinstance(v, list) else str(v)


def _num(v, typ):
    try:
        return typ(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def metric_values(item: dict) -> dict:
    # Play sessions / time spent come nested per channel, e.g. {"portal": 4133}: keep as JSON.
    def as_json(v):
        return json.dumps(v) if v is not None else None

    return {
        "play_sessions_json": as_json(item.get("me_totalPlaySessionCount")),
        "time_spent_json": as_json(item.get("me_totalTimeSpentInSec")),
        "avg_rating": _num(item.get("me_averageRating"), float),
        "ratings_count": _num(item.get("me_totalRatingsCount"), int),
        "downloads": _num(item.get("me_totalDownloads"), int),
    }


def content_row(item: dict, slice_id: str, mode: str, load_id: str, ingested_at: str) -> dict:
    row = {f: _as_str(item.get(f)) for f in STRING_FIELDS}
    row |= {f: _as_list(item.get(f)) for f in ARRAY_FIELDS}
    row |= metric_values(item)
    row |= {"slice_id": slice_id, "_mode": mode, "_load_id": load_id, "_ingested_at": ingested_at}
    return row


def metrics_row(item: dict, snapshot_date: str, load_id: str, ingested_at: str) -> dict:
    return {
        "snapshot_date": snapshot_date,
        "identifier": _as_str(item.get("identifier")),
        **metric_values(item),
        "_load_id": load_id,
        "_ingested_at": ingested_at,
    }


# ------------------------------------------------------------------ BigQuery
class BigQuerySink:
    def __init__(self):
        from google.cloud import bigquery  # imported here so --dry-run works without it

        self.bq = bigquery
        self.client = bigquery.Client(location=BQ_LOCATION)
        self._schemas: dict[str, list] = {}

    def _table_id(self, name: str) -> str:
        return f"{self.client.project}.{name}"

    def load(self, name: str, rows: list[dict]) -> None:
        """Append rows with a batch load job (free, unlike streaming inserts)."""
        if not rows:
            return
        table_id = self._table_id(name)
        if table_id not in self._schemas:
            self._schemas[table_id] = self.client.get_table(table_id).schema
        config = self.bq.LoadJobConfig(
            schema=self._schemas[table_id],
            write_disposition=self.bq.WriteDisposition.WRITE_APPEND,
        )
        self.client.load_table_from_json(rows, table_id, job_config=config).result()

    def last_success(self, modes: list[str]) -> datetime | None:
        sql = f"""
            SELECT MAX(started_at) AS ts FROM `{self._table_id("ops.ingestion_log")}`
            WHERE source = 'diksha' AND status = 'success' AND mode IN UNNEST(@modes)
        """
        params = [self.bq.ArrayQueryParameter("modes", "STRING", modes)]
        job = self.client.query(sql, job_config=self.bq.QueryJobConfig(query_parameters=params))
        return next(iter(job.result())).ts


# ------------------------------------------------------------------ run
def run_mode(mode, *, limit=None, dry_run=False, since=None, diksha=None, sink=None) -> dict:
    started = datetime.now(UTC)
    run_id = uuid.uuid4().hex[:12]
    ingested_at = started.isoformat()
    sink = None if dry_run else (sink or BigQuerySink())
    diksha = diksha or Diksha()
    rows_loaded = slices_done = 0
    watermark = None
    tomorrow = started.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)

    try:
        if mode == "incremental":
            if since is None:
                last = sink.last_success(["full", "incremental"]) if sink else None
                if sink and last is None:
                    raise RuntimeError(
                        "no successful full/incremental run yet: run mode=full first"
                    )
                since = (last or started) - INCREMENTAL_OVERLAP
            watermark = fmt_ts(since)
            field, start = "lastUpdatedOn", since
        else:
            field, start = "createdOn", FULL_START

        fields = METRICS_ONLY_FIELDS if mode == "metrics" else CONTENT_FIELDS
        for slc in iter_slices(diksha.count, BASE_FILTERS, field, start, tomorrow):
            if slc.over_cap:
                log.warning("slice %s has %d items: over the cap", slc.slice_id, slc.expected_count)
            items = fetch_slice(diksha, slc, fields)
            if limit is not None:
                items = items[: limit - rows_loaded]
            if mode == "metrics":
                table = "raw.diksha_metrics_snapshot"
                snapshot_date = started.date().isoformat()
                rows = [metrics_row(i, snapshot_date, run_id, ingested_at) for i in items]
            else:
                table = "raw.diksha_content"
                rows = [content_row(i, slc.slice_id, mode, run_id, ingested_at) for i in items]
            if sink:
                sink.load(table, rows)
            rows_loaded += len(rows)
            slices_done += 1
            log.info("%s %s: %d rows (total %d)", mode, slc.slice_id, len(rows), rows_loaded)
            if limit is not None and rows_loaded >= limit:
                break
        status = "partial" if limit is not None else "success"
        error = None
    except Exception as e:  # logged to ops.ingestion_log, so a failure is visible, not silent
        log.exception("diksha %s run failed", mode)
        status, error = "failed", f"{type(e).__name__}: {e}"[:1000]

    summary = {
        "run_id": run_id,
        "source": "diksha",
        "mode": mode,
        "started_at": ingested_at,
        "finished_at": datetime.now(UTC).isoformat(),
        "status": status,
        "rows_loaded": rows_loaded,
        "slices": slices_done,
        "watermark": watermark,
        "error_message": error,
    }
    if sink:
        try:
            sink.load("ops.ingestion_log", [summary])
        except Exception:
            log.exception("could not write ops.ingestion_log")
    log.info("summary: %s", summary)
    return summary


def run_modes(mode: str, **kwargs) -> list[dict]:
    if mode not in (*MODES, "daily"):
        raise ValueError(f"unknown mode {mode!r}; use one of {', '.join((*MODES, 'daily'))}")
    modes = ["incremental", "metrics"] if mode == "daily" else [mode]
    return [run_mode(m, **kwargs) for m in modes]


def _parse_since(value) -> datetime | None:
    return datetime.fromisoformat(value).replace(tzinfo=UTC) if value else None


def run(request):
    """HTTP entry point for Cloud Run functions."""
    params = {**request.args, **(request.get_json(silent=True) or {})}
    try:
        results = run_modes(
            params.get("mode", "daily"),
            limit=int(params["limit"]) if params.get("limit") else None,
            dry_run=str(params.get("dry_run", "false")).lower() in ("1", "true", "yes"),
            since=_parse_since(params.get("since")),
        )
    except ValueError as e:
        return json.dumps({"error": str(e)}), 400, {"Content-Type": "application/json"}
    failed = any(r["status"] == "failed" for r in results)
    return (
        json.dumps(results, indent=2),
        500 if failed else 200,
        {"Content-Type": "application/json"},
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="DIKSHA -> BigQuery ingestion")
    ap.add_argument("--mode", default="daily", choices=[*MODES, "daily"])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--since", help="YYYY-MM-DD, incremental start date")
    a = ap.parse_args()
    run_modes(a.mode, limit=a.limit, dry_run=a.dry_run, since=_parse_since(a.since))
