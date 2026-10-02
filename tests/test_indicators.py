"""Indicators function tests: fake HTTP responses, no network, no BigQuery."""

import importlib.util
import json
from pathlib import Path

import pytest

# Loaded by path: every Cloud Run function folder has its own main.py.
_spec = importlib.util.spec_from_file_location(
    "indicators_main", Path(__file__).parents[1] / "functions" / "indicators" / "main.py"
)
ind = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ind)

WB_RECORD = {
    "indicator": {"id": "SE.PRM.ENRR", "value": "School enrollment, primary (% gross)"},
    "country": {"id": "IN", "value": "India"},
    "countryiso3code": "IND",
    "date": "2025",
    "value": 111.03,
    "unit": "",
    "obs_status": "",
    "decimal": 0,
}


class FakeResponse:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    def json(self):
        return self.body

    def raise_for_status(self):
        pass


class FakeSession:
    """Answers World Bank with 2 pages per indicator and UIS with 2 records."""

    def __init__(self):
        self.headers = {}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(url)
        if "worldbank" in url:
            page = params["page"]
            null_year = {**WB_RECORD, "date": "2022", "value": None}
            return FakeResponse(
                [{"page": page, "pages": 2}, [WB_RECORD if page == 1 else null_year]]
            )
        return FakeResponse(
            {
                "records": [
                    {"indicatorId": "CR.1", "geoUnit": "IND", "year": 2019, "value": 94.15},
                    {"indicatorId": "CR.2", "geoUnit": "IND", "year": 2019, "value": None},
                ],
                "indicatorMetadata": [
                    {
                        "indicatorCode": "CR.1",
                        "name": "Completion rate, primary",
                        "theme": "EDUCATION",
                        "lastDataUpdate": "02/09/2026",
                        "lastDataUpdateDescription": "February 2026 Data Release",
                    }
                ],
            }
        )


class FakeSink:
    def __init__(self):
        self.tables: dict[str, list] = {}

    def load(self, name, rows):
        self.tables.setdefault(name, []).extend(rows)


def test_worldbank_row_keeps_null_years_and_blanks_as_none():
    row = ind.worldbank_row({**WB_RECORD, "date": "2022", "value": None})
    assert row["year"] == 2022 and row["value"] is None
    assert row["unit"] is None and row["country_iso3"] == "IND"


def test_worldbank_row_keeps_every_field_and_original_json():
    row = ind.worldbank_row(WB_RECORD, "2026-07-13")
    assert row["country_id"] == "IN" and row["country_name"] == "India"
    assert row["source_last_updated"] == "2026-07-13"
    assert json.loads(row["record_json"]) == WB_RECORD


def test_worldbank_follows_pages_for_every_indicator():
    session = FakeSession()
    rows = ind.fetch_worldbank(session)
    assert len(rows) == 2 * len(ind.WB_INDICATORS)
    assert len(session.calls) == 2 * len(ind.WB_INDICATORS)


def test_worldbank_error_message_raises():
    class ErrSession(FakeSession):
        def get(self, url, params=None, timeout=None):
            return FakeResponse([{"message": [{"value": "Invalid indicator"}]}])

    with pytest.raises(ValueError):
        ind.fetch_worldbank(ErrSession())


def test_uis_rows_get_indicator_metadata_and_original_json():
    rows = ind.fetch_uis(FakeSession())
    assert rows[0]["indicator_name"] == "Completion rate, primary"
    assert rows[0]["indicator_theme"] == "EDUCATION"
    assert rows[0]["last_data_update_description"] == "February 2026 Data Release"
    assert json.loads(rows[0]["record_json"])["value"] == 94.15
    # CR.2 has no metadata in the fake response: metadata columns stay empty
    assert rows[1]["indicator_name"] is None and rows[1]["indicator_metadata_json"] is None


def test_run_source_loads_rows_and_logs():
    sink = FakeSink()
    summary = ind.run_source("uis", session=FakeSession(), sink=sink)

    assert summary["status"] == "success" and summary["rows_loaded"] == 2
    loaded = sink.tables["raw.uis_indicators"]
    assert all(r["_load_id"] == summary["run_id"] for r in loaded)
    assert sink.tables["ops.ingestion_log"][0]["source"] == "uis"


def test_failure_is_logged_not_raised():
    class Broken(FakeSession):
        def get(self, url, params=None, timeout=None):
            raise RuntimeError("API down")

    sink = FakeSink()
    summary = ind.run_source("uis", session=Broken(), sink=sink)
    assert summary["status"] == "failed" and "API down" in summary["error_message"]
    assert sink.tables["ops.ingestion_log"][0]["status"] == "failed"
