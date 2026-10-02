"""UNESCO UIS + World Bank -> BigQuery, deployed as a Cloud Run function (entry point: `run`).

Fetches a fixed list of national education indicators for India and four neighbours from
both APIs (no key needed) and appends every value to raw tables. Each run reloads the full
history (~1-2K rows), so there is no watermark: staging keeps the latest load per value.
Both sources publish about once a year, so a monthly schedule is plenty.

Run locally (no BigQuery needed with --dry-run):
  python functions/indicators/main.py --dry-run
"""

import argparse
import json
import logging
import os
import uuid
from collections.abc import Callable
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
log = logging.getLogger("indicators")

USER_AGENT = "learning-content-pulse/0.1 (public-data portfolio project)"
BQ_LOCATION = os.environ.get("BQ_LOCATION", "asia-south1")

# India + peers for comparison: Bangladesh, Pakistan, Nepal, Sri Lanka.
COUNTRIES = ["IND", "BGD", "PAK", "NPL", "LKA"]

WB_URL = "https://api.worldbank.org/v2/country/{countries}/indicator/{indicator}"
WB_INDICATORS = [
    "SE.PRM.ENRR",  # School enrollment, primary (% gross)
    "SE.SEC.ENRR",  # School enrollment, secondary (% gross)
    "SE.PRM.CMPT.ZS",  # Primary completion rate
    "SE.SEC.CMPT.LO.ZS",  # Lower secondary completion rate
    "SE.PRM.UNER",  # Children out of school, primary
    "SE.ADT.LITR.ZS",  # Adult literacy rate
    "SE.PRM.ENRL.TC.ZS",  # Pupil-teacher ratio, primary
    "SE.XPD.TOTL.GD.ZS",  # Government expenditure on education (% of GDP)
]

UIS_URL = "https://api.uis.unesco.org/api/public/data/indicators"
UIS_INDICATORS = [
    "CR.1",  # Completion rate, primary education
    "CR.2",  # Completion rate, lower secondary education
    "ROFST.1.CP",  # Out-of-school rate, primary
]


# ------------------------------------------------------------------ HTTP
class RetryableHTTPError(requests.HTTPError):
    """Rate limited or server-side failure: worth retrying."""


# World Bank often times out once or twice, then answers in < 1 s (seen 1 Oct 2026),
# so a short timeout plus retries beats one long wait.
@retry(
    retry=retry_if_exception_type((RetryableHTTPError, requests.ConnectionError, requests.Timeout)),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(6),
    before_sleep=before_sleep_log(log, logging.WARNING),
    reraise=True,
)
def get_json(session: requests.Session, url: str, params: dict):
    resp = session.get(url, params=params, timeout=30)
    if resp.status_code in {429, 500, 502, 503, 504}:
        raise RetryableHTTPError(f"HTTP {resp.status_code} from {url}", response=resp)
    resp.raise_for_status()
    return resp.json()


def _float(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _int(v):
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ World Bank
def worldbank_row(d: dict, source_last_updated: str | None = None) -> dict:
    """Every field of a World Bank record, flattened, plus the original record as JSON."""
    country = d.get("country") or {}
    return {
        "indicator_id": d["indicator"]["id"],
        "indicator_name": d["indicator"].get("value"),
        "country_id": country.get("id"),
        "country_name": country.get("value"),
        "country_iso3": d.get("countryiso3code") or None,
        "year": _int(d.get("date")),
        "value": _float(d.get("value")),  # missing years come back as null: keep them
        "unit": d.get("unit") or None,
        "obs_status": d.get("obs_status") or None,
        "decimal": _int(d.get("decimal")),
        "source_last_updated": source_last_updated,  # from the page header, e.g. 2026-07-13
        "record_json": json.dumps(d, ensure_ascii=False),
    }


def fetch_worldbank(session: requests.Session) -> list[dict]:
    rows = []
    url_countries = ";".join(COUNTRIES)
    for indicator in WB_INDICATORS:
        url = WB_URL.format(countries=url_countries, indicator=indicator)
        page = 1
        while True:
            body = get_json(session, url, {"format": "json", "per_page": 1000, "page": page})
            # Errors (e.g. an unknown indicator) come back as a 1-element list with a message.
            if not isinstance(body, list) or len(body) < 2:
                raise ValueError(f"unexpected World Bank response for {indicator}: {body}")
            meta, data = body
            rows.extend(worldbank_row(d, meta.get("lastupdated")) for d in data or [])
            if page >= (meta.get("pages") or 1):
                break
            page += 1
        log.info("worldbank %s: %d rows so far", indicator, len(rows))
    return rows


# ------------------------------------------------------------------ UNESCO UIS
def uis_row(r: dict, metadata: dict) -> dict:
    """Every field of a UIS record, plus its indicator's metadata, plus the original JSON."""
    meta = metadata.get(r.get("indicatorId")) or {}
    return {
        "indicator_id": r.get("indicatorId"),
        "indicator_name": meta.get("name"),
        "geo_unit": r.get("geoUnit"),
        "year": _int(r.get("year")),
        "value": _float(r.get("value")),
        "magnitude": r.get("magnitude"),
        "qualifier": r.get("qualifier"),
        "indicator_theme": meta.get("theme"),
        "last_data_update": meta.get("lastDataUpdate"),  # e.g. 02/09/2026
        "last_data_update_description": meta.get("lastDataUpdateDescription"),
        "indicator_metadata_json": json.dumps(meta, ensure_ascii=False) if meta else None,
        "record_json": json.dumps(r, ensure_ascii=False),
    }


def fetch_uis(session: requests.Session) -> list[dict]:
    params = {"indicator": UIS_INDICATORS, "geoUnit": COUNTRIES, "indicatorMetadata": "true"}
    body = get_json(session, UIS_URL, params)
    metadata = {m["indicatorCode"]: m for m in body.get("indicatorMetadata") or []}
    return [uis_row(r, metadata) for r in body.get("records") or []]


SOURCES: dict[str, tuple[Callable, str]] = {
    "worldbank": (fetch_worldbank, "raw.worldbank_indicators"),
    "uis": (fetch_uis, "raw.uis_indicators"),
}


# ------------------------------------------------------------------ BigQuery
class BigQuerySink:
    def __init__(self):
        from google.cloud import bigquery  # imported here so --dry-run works without it

        self.bq = bigquery
        self.client = bigquery.Client(location=BQ_LOCATION)
        self._schemas: dict[str, list] = {}

    def load(self, name: str, rows: list[dict]) -> None:
        """Append rows with a batch load job (free, unlike streaming inserts)."""
        if not rows:
            return
        table_id = f"{self.client.project}.{name}"
        if table_id not in self._schemas:
            self._schemas[table_id] = self.client.get_table(table_id).schema
        config = self.bq.LoadJobConfig(
            schema=self._schemas[table_id],
            write_disposition=self.bq.WriteDisposition.WRITE_APPEND,
        )
        self.client.load_table_from_json(rows, table_id, job_config=config).result()


# ------------------------------------------------------------------ run
def run_source(source: str, *, dry_run=False, session=None, sink=None) -> dict:
    fetch, table = SOURCES[source]
    started = datetime.now(UTC)
    run_id = uuid.uuid4().hex[:12]
    sink = None if dry_run else (sink or BigQuerySink())
    session = session or requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    rows: list[dict] = []
    try:
        rows = fetch(session)
        for r in rows:
            r["_load_id"] = run_id
            r["_ingested_at"] = started.isoformat()
        if sink:
            sink.load(table, rows)
        status, error = "success", None
    except Exception as e:  # logged to ops.ingestion_log, so a failure is visible, not silent
        log.exception("%s run failed", source)
        rows = []
        status, error = "failed", f"{type(e).__name__}: {e}"[:1000]

    summary = {
        "run_id": run_id,
        "source": source,
        "mode": "full",
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "status": status,
        "rows_loaded": len(rows),
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


def run_all(dry_run=False) -> list[dict]:
    # One source failing must not stop the other.
    return [run_source(s, dry_run=dry_run) for s in SOURCES]


def run(request):
    """HTTP entry point for Cloud Run functions."""
    params = {**request.args, **(request.get_json(silent=True) or {})}
    dry_run = str(params.get("dry_run", "false")).lower() in ("1", "true", "yes")
    results = run_all(dry_run=dry_run)
    failed = any(r["status"] == "failed" for r in results)
    return (
        json.dumps(results, indent=2),
        500 if failed else 200,
        {"Content-Type": "application/json"},
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="UNESCO UIS + World Bank -> BigQuery")
    ap.add_argument("--dry-run", action="store_true")
    run_all(dry_run=ap.parse_args().dry_run)
