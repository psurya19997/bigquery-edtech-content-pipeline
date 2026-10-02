"""Google Sheet "Localization Plan" -> BigQuery, deployed as a Cloud Run function (entry: `run`).

Each run reads every row of the sheet's `plan` tab with the Google Sheets API and appends a
full copy to raw.localization_plan_snapshot. Values are stored exactly as typed (text):
cleaning happens later in staging, so mess like "Marathi ", "forty" or "15/12/2026" survives.
Every run is a new snapshot, so edits to the sheet build up a history.

Access: the function runs as the service account `ingestion-sa`; the sheet must be shared
with ingestion-sa@learning-reach-473210.iam.gserviceaccount.com as Viewer. No key needed.

Options (?name=value or JSON body): dry_run=true.
Run locally (needs `gcloud auth application-default login` with access to the sheet):
  python functions/sheets/main.py --dry-run
"""

import argparse
import json
import logging
import os
import uuid
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
log = logging.getLogger("sheets")

BQ_LOCATION = os.environ.get("BQ_LOCATION", "asia-south1")
SHEET_ID = os.environ.get("SHEET_ID", "1L6jXt19Rl7xMNGVa-5WIWfZNnfluikVIng10VyTe2H0")
SHEET_TAB = os.environ.get("SHEET_TAB", "plan")
SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

COLUMNS = [
    "plan_id", "language", "board", "grade", "subject", "resources_planned",
    "owner", "status", "target_date", "last_updated", "notes",
]  # fmt: skip
TABLE = "raw.localization_plan_snapshot"


# ------------------------------------------------------------------ Sheets API
class RetryableHTTPError(requests.HTTPError):
    """Rate limited or server-side failure: worth retrying."""


def authorized_session():
    """HTTP session that signs requests as the runtime service account (ingestion-sa)."""
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    credentials, _ = google.auth.default(scopes=SCOPES)
    return AuthorizedSession(credentials)


@retry(
    retry=retry_if_exception_type((RetryableHTTPError, requests.ConnectionError, requests.Timeout)),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(6),
    before_sleep=before_sleep_log(log, logging.WARNING),
    reraise=True,
)
def fetch_values(session, sheet_id: str, tab: str) -> list[list[str]]:
    resp = session.get(
        f"{SHEETS_API}/{sheet_id}/values/{tab}",
        # Exactly what a person sees in the cell, as text; nothing reinterpreted.
        params={"valueRenderOption": "FORMATTED_VALUE", "majorDimension": "ROWS"},
        timeout=30,
    )
    if resp.status_code in {429, 500, 502, 503, 504}:
        raise RetryableHTTPError(f"HTTP {resp.status_code} from Sheets API", response=resp)
    if resp.status_code == 403:
        raise PermissionError(
            "HTTP 403 from Sheets API: share the sheet with "
            "ingestion-sa@learning-reach-473210.iam.gserviceaccount.com (Viewer)"
        )
    if resp.status_code == 404:
        raise LookupError(f"HTTP 404: no sheet {sheet_id!r} or no tab {tab!r}")
    resp.raise_for_status()
    return resp.json().get("values") or []


# ------------------------------------------------------------------ rows
def sheet_rows(values: list[list[str]]) -> list[dict]:
    """Turn the raw grid into one dict per data row.

    - The first row must contain every expected column (in any order; extra columns are
      kept in row_json). A renamed or missing column fails loudly instead of loading junk.
    - The API drops trailing empty cells, so short rows are padded with None.
    - Completely empty rows are skipped. Nothing is trimmed or converted.
    """
    if not values:
        raise ValueError("the sheet tab is empty (no header row)")
    header = values[0]
    missing = [c for c in COLUMNS if c not in header]
    if missing:
        raise ValueError(f"sheet header is missing column(s) {missing}; found {header}")

    rows = []
    for sheet_row_number, cells in enumerate(values[1:], start=2):
        if not any(str(c).strip() for c in cells):
            continue
        record = {h: (cells[i] if i < len(cells) and cells[i] != "" else None)
                  for i, h in enumerate(header)}  # fmt: skip
        rows.append(
            {
                **{c: record.get(c) for c in COLUMNS},
                "_row_number": sheet_row_number,
                "row_json": json.dumps(record, ensure_ascii=False),
            }
        )
    return rows


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
def run_snapshot(*, dry_run=False, session=None, sink=None) -> dict:
    started = datetime.now(UTC)
    run_id = uuid.uuid4().hex[:12]
    sink = None if dry_run else (sink or BigQuerySink())
    rows: list[dict] = []
    try:
        session = session or authorized_session()
        rows = sheet_rows(fetch_values(session, SHEET_ID, SHEET_TAB))
        for r in rows:
            r |= {"_sheet_id": SHEET_ID, "_load_id": run_id, "_ingested_at": started.isoformat()}
        if sink:
            sink.load(TABLE, rows)
        log.info("%d sheet rows written to %s", len(rows), TABLE)
        status, error = "success", None
    except Exception as e:  # logged to ops.ingestion_log, so a failure is visible, not silent
        log.exception("sheets run failed")
        rows = []
        status, error = "failed", f"{type(e).__name__}: {e}"[:1000]

    summary = {
        "run_id": run_id,
        "source": "sheets",
        "mode": "snapshot",
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


def run(request):
    """HTTP entry point for Cloud Run functions."""
    params = {**request.args, **(request.get_json(silent=True) or {})}
    dry_run = str(params.get("dry_run", "false")).lower() in ("1", "true", "yes")
    summary = run_snapshot(dry_run=dry_run)
    return (
        json.dumps(summary, indent=2),
        500 if summary["status"] == "failed" else 200,
        {"Content-Type": "application/json"},
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Google Sheet -> BigQuery")
    ap.add_argument("--dry-run", action="store_true")
    run_snapshot(dry_run=ap.parse_args().dry_run)
