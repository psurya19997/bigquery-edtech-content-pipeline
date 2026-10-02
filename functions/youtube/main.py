"""YouTube Data API v3 -> BigQuery, deployed as a Cloud Run function (entry point: `run`).

Each run, for every channel in CHANNELS:
  1. channels.list       -> channel totals         -> raw.youtube_channel_snapshot (1 row/day)
  2. playlistItems.list  -> every uploaded video ID (via the channel's "uploads" playlist)
  3. videos.list         -> details + statistics for all of them, 50 per call
       stats of every video       -> raw.youtube_video_stats_snapshot (1 row/video/day)
       details of videos new to us -> raw.youtube_videos (all videos if refresh_details=true)

YouTube only shows today's totals, so view history starts from the first snapshot.
Quota: 1 unit per call (~1 + videos/50 + videos/50 per channel), far below 10,000/day.

The API key comes from the YOUTUBE_API_KEY environment variable, which Cloud Run fills from
the Secret Manager secret `youtube-api-key` (never put the key in code).

Options (?name=value or JSON body): refresh_details=true, dry_run=true.
Run locally:  set YOUTUBE_API_KEY first, then  python functions/youtube/main.py --dry-run
"""

import argparse
import json
import logging
import os
import re
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import requests
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("youtube")

API = "https://www.googleapis.com/youtube/v3"
BQ_LOCATION = os.environ.get("BQ_LOCATION", "asia-south1")

CHANNELS = {
    "UCU0kWLAbhVGxXarmE3b8rHg": "Khan Academy India",
    "UCg4BkaHyyE_4-RvEMJ2PTtA": "Khan Academy India - English",
}
CHANNEL_PARTS = "snippet,statistics,contentDetails,brandingSettings,topicDetails,status"
# videos.list costs 1 unit no matter how many parts are requested, so ask for all useful ones.
VIDEO_PARTS = "snippet,contentDetails,statistics,status,topicDetails,localizations,recordingDetails"
DETAIL_CHUNK = 500  # video detail rows per BigQuery load job


# ------------------------------------------------------------------ HTTP
class RetryableHTTPError(requests.HTTPError):
    """Server-side failure: worth retrying. (403 quotaExceeded is not: retrying won't help.)"""


@retry(
    retry=retry_if_exception_type((RetryableHTTPError, requests.ConnectionError, requests.Timeout)),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(6),
    before_sleep=before_sleep_log(log, logging.WARNING),
    reraise=True,
)
def get_json(session: requests.Session, endpoint: str, params: dict, key: str) -> dict:
    resp = session.get(f"{API}/{endpoint}", params={**params, "key": key}, timeout=30)
    if resp.status_code in {500, 502, 503, 504}:
        raise RetryableHTTPError(f"HTTP {resp.status_code} from YouTube {endpoint}", response=resp)
    if resp.status_code >= 400:
        # The key is in the URL, so never log the URL; YouTube's error message is enough.
        reason = resp.json().get("error", {}).get("message", resp.text[:300])
        raise requests.HTTPError(f"HTTP {resp.status_code} from YouTube {endpoint}: {reason}")
    return resp.json()


def _int(v):
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _json(v):
    return json.dumps(v, ensure_ascii=False) if v else None


def duration_seconds(iso: str | None) -> int | None:
    """ISO 8601 duration (PT1H2M3S, P1DT2H) -> seconds."""
    if not iso:
        return None
    m = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", iso)
    if not m:
        return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


# ------------------------------------------------------------------ API calls
def fetch_channels(session, key) -> list[dict]:
    body = get_json(session, "channels", {"part": CHANNEL_PARTS, "id": ",".join(CHANNELS)}, key)
    return body.get("items") or []


def fetch_upload_ids(session, key, uploads_playlist_id: str) -> list[str]:
    ids, page_token = [], None
    while True:
        params = {"part": "contentDetails", "playlistId": uploads_playlist_id, "maxResults": 50}
        if page_token:
            params["pageToken"] = page_token
        body = get_json(session, "playlistItems", params, key)
        ids.extend(i["contentDetails"]["videoId"] for i in body.get("items") or [])
        page_token = body.get("nextPageToken")
        if not page_token:
            return ids


def iter_video_batches(session, key, video_ids: list[str]) -> Iterator[list[dict]]:
    """Yield video resources 50 at a time, so callers never hold thousands in memory.
    Private or deleted videos are silently left out by YouTube."""
    for i in range(0, len(video_ids), 50):
        batch = ",".join(video_ids[i : i + 50])
        yield get_json(session, "videos", {"part": VIDEO_PARTS, "id": batch}, key)["items"]


# ------------------------------------------------------------------ rows
def channel_row(c: dict, snapshot_date: str) -> dict:
    sn, st = c.get("snippet") or {}, c.get("statistics") or {}
    branding = (c.get("brandingSettings") or {}).get("channel") or {}
    return {
        "snapshot_date": snapshot_date,
        "channel_id": c["id"],
        "title": sn.get("title"),
        "description": sn.get("description"),
        "custom_url": sn.get("customUrl"),
        "published_at": sn.get("publishedAt"),
        "country": sn.get("country"),
        "default_language": sn.get("defaultLanguage"),
        "view_count": _int(st.get("viewCount")),
        "subscriber_count": _int(st.get("subscriberCount")),  # rounded by YouTube
        "hidden_subscriber_count": st.get("hiddenSubscriberCount"),
        "video_count": _int(st.get("videoCount")),
        "uploads_playlist_id": (c.get("contentDetails") or {})
        .get("relatedPlaylists", {})
        .get("uploads"),
        "keywords": branding.get("keywords"),
        "topic_categories": (c.get("topicDetails") or {}).get("topicCategories") or [],
        "privacy_status": (c.get("status") or {}).get("privacyStatus"),
        "made_for_kids": (c.get("status") or {}).get("madeForKids"),
        "record_json": _json(c),
    }


def video_stats_row(v: dict, snapshot_date: str) -> dict:
    st = v.get("statistics") or {}
    return {
        "snapshot_date": snapshot_date,
        "video_id": v["id"],
        "channel_id": (v.get("snippet") or {}).get("channelId"),
        "view_count": _int(st.get("viewCount")),
        "like_count": _int(st.get("likeCount")),  # missing when the owner hides likes
        "comment_count": _int(st.get("commentCount")),  # missing when comments are off
    }


def video_row(v: dict) -> dict:
    sn, cd = v.get("snippet") or {}, v.get("contentDetails") or {}
    status, st = v.get("status") or {}, v.get("statistics") or {}
    return {
        "video_id": v["id"],
        "etag": v.get("etag"),
        "channel_id": sn.get("channelId"),
        "channel_title": sn.get("channelTitle"),
        "published_at": sn.get("publishedAt"),
        "title": sn.get("title"),
        "description": sn.get("description"),
        "tags": sn.get("tags") or [],
        "category_id": sn.get("categoryId"),
        "default_language": sn.get("defaultLanguage"),
        "default_audio_language": sn.get("defaultAudioLanguage"),
        "live_broadcast_content": sn.get("liveBroadcastContent"),
        "duration": cd.get("duration"),
        "duration_seconds": duration_seconds(cd.get("duration")),
        "dimension": cd.get("dimension"),
        "definition": cd.get("definition"),
        "caption": cd.get("caption") == "true",  # YouTube sends the string "true"/"false"
        "licensed_content": cd.get("licensedContent"),
        "projection": cd.get("projection"),
        "content_rating_json": _json(cd.get("contentRating")),
        "region_restriction_json": _json(cd.get("regionRestriction")),
        "upload_status": status.get("uploadStatus"),
        "privacy_status": status.get("privacyStatus"),
        "license": status.get("license"),
        "embeddable": status.get("embeddable"),
        "public_stats_viewable": status.get("publicStatsViewable"),
        "made_for_kids": status.get("madeForKids"),
        "topic_categories": (v.get("topicDetails") or {}).get("topicCategories") or [],
        "localizations_json": _json(v.get("localizations")),
        "recording_date": (v.get("recordingDetails") or {}).get("recordingDate"),
        "view_count": _int(st.get("viewCount")),
        "like_count": _int(st.get("likeCount")),
        "comment_count": _int(st.get("commentCount")),
        "record_json": _json(v),
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

    def known_video_ids(self) -> set[str]:
        sql = f"SELECT DISTINCT video_id FROM `{self._table_id('raw.youtube_videos')}`"
        return {r.video_id for r in self.client.query(sql).result()}


# ------------------------------------------------------------------ run
def run_snapshot(*, refresh_details=False, dry_run=False, session=None, sink=None, key=None):
    started = datetime.now(UTC)
    run_id = uuid.uuid4().hex[:12]
    stamp = {"_load_id": run_id, "_ingested_at": started.isoformat()}
    snapshot_date = started.date().isoformat()
    sink = None if dry_run else (sink or BigQuerySink())
    session = session or requests.Session()
    rows_loaded = 0
    try:
        key = key or os.environ.get("YOUTUBE_API_KEY")
        if not key:
            raise RuntimeError("YOUTUBE_API_KEY is not set (Cloud Run: reference the secret)")

        channels = fetch_channels(session, key)
        missing = set(CHANNELS) - {c["id"] for c in channels}
        if missing:
            log.warning("channels not returned by YouTube: %s", missing)
        channel_rows = [{**channel_row(c, snapshot_date), **stamp} for c in channels]
        known = set() if (refresh_details or not sink) else sink.known_video_ids()

        # Video resources are big (descriptions, translations, original JSON): turn each batch
        # of 50 into rows straight away and flush details every DETAIL_CHUNK videos, so memory
        # stays flat. (Holding all 5,758 videos at once used > 1 GiB on 2 Oct 2026.)
        stats_rows: list[dict] = []  # small: 6 numbers per video
        detail_buffer: list[dict] = []
        details_written = 0

        def flush_details():
            nonlocal details_written
            if sink:
                sink.load("raw.youtube_videos", detail_buffer)
            details_written += len(detail_buffer)
            detail_buffer.clear()

        for row in channel_rows:
            ids = fetch_upload_ids(session, key, row["uploads_playlist_id"])
            public = 0
            for batch in iter_video_batches(session, key, ids):
                public += len(batch)
                stats_rows.extend({**video_stats_row(v, snapshot_date), **stamp} for v in batch)
                detail_buffer.extend(
                    {**video_row(v), **stamp} for v in batch if v["id"] not in known
                )
                if len(detail_buffer) >= DETAIL_CHUNK:
                    flush_details()
            log.info("%s: %d uploads, %d public", row["title"], len(ids), public)
        flush_details()

        if sink:
            sink.load("raw.youtube_channel_snapshot", channel_rows)
            sink.load("raw.youtube_video_stats_snapshot", stats_rows)
        rows_loaded = len(stats_rows)
        log.info(
            "%d channels, %d video stats, %d video details written",
            len(channel_rows),
            len(stats_rows),
            details_written,
        )
        status, error = "success", None
    except Exception as e:  # logged to ops.ingestion_log, so a failure is visible, not silent
        log.exception("youtube run failed")
        status, error = "failed", f"{type(e).__name__}: {e}"[:1000]

    summary = {
        "run_id": run_id,
        "source": "youtube",
        "mode": "snapshot",
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "status": status,
        "rows_loaded": rows_loaded,
        "slices": None,
        "watermark": None,
        "error_message": error,
    }
    if sink:
        try:
            sink.load("ops.ingestion_log", [summary])
        except Exception:
            log.exception("could not write ops.ingestion_log")
    log.info("summary: %s", summary)
    return summary


def _flag(v) -> bool:
    return str(v).lower() in ("1", "true", "yes")


def run(request):
    """HTTP entry point for Cloud Run functions."""
    params = {**request.args, **(request.get_json(silent=True) or {})}
    summary = run_snapshot(
        refresh_details=_flag(params.get("refresh_details", "false")),
        dry_run=_flag(params.get("dry_run", "false")),
    )
    return (
        json.dumps(summary, indent=2),
        500 if summary["status"] == "failed" else 200,
        {"Content-Type": "application/json"},
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="YouTube Data API -> BigQuery")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--refresh-details", action="store_true")
    a = ap.parse_args()
    run_snapshot(refresh_details=a.refresh_details, dry_run=a.dry_run)
