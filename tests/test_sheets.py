"""Sheets function tests: the real seed CSV as a fake sheet, no network, no BigQuery."""

import csv
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
_spec = importlib.util.spec_from_file_location("sheets_main", ROOT / "functions/sheets/main.py")
sh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sh)


def seed_grid() -> list[list[str]]:
    """The seed CSV as the Sheets API would return it (trailing empty cells dropped)."""
    with open(ROOT / "reference/localization_plan_seed.csv", newline="", encoding="utf-8") as f:
        grid = list(csv.reader(f))
    for row in grid:
        while row and row[-1] == "":
            row.pop()
    return grid


class FakeResponse:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    def json(self):
        return self.body

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self, grid=None, status=200):
        self.grid, self.status = grid, status

    def get(self, url, params=None, timeout=None):
        return FakeResponse({"values": self.grid}, self.status)


class FakeSink:
    def __init__(self):
        self.tables: dict[str, list] = {}

    def load(self, name, rows):
        self.tables.setdefault(name, []).extend(rows)


def test_seed_rows_keep_every_planted_problem_exactly_as_typed():
    rows = sh.sheet_rows(seed_grid())
    by_row = {r["_row_number"]: r for r in rows}

    assert len(rows) == 25
    assert by_row[4]["language"] == "Marathi "  # trailing space survives
    assert by_row[13]["grade"] == "6" and by_row[14]["grade"] == "VII"
    assert by_row[15]["target_date"] == "15/12/2026"
    assert by_row[16]["status"] is None  # blank cell -> NULL
    assert by_row[17]["resources_planned"] == "forty"
    assert by_row[3]["notes"] is None  # trailing empty cell dropped by the API -> NULL
    assert sum(r["plan_id"] == "LP-005" for r in rows) == 2  # duplicate kept for staging


def test_row_json_holds_the_full_row():
    row = sh.sheet_rows(seed_grid())[0]
    assert json.loads(row["row_json"])["plan_id"] == "LP-001"


def test_empty_rows_are_skipped_and_extra_columns_kept_in_json():
    grid = [
        sh.COLUMNS + ["priority"],
        ["LP-1", "Hindi"],
        [],
        ["", "  "],
        ["LP-2"] + [""] * 10 + ["high"],
    ]
    rows = sh.sheet_rows(grid)

    assert [r["plan_id"] for r in rows] == ["LP-1", "LP-2"]
    assert [r["_row_number"] for r in rows] == [2, 5]
    assert json.loads(rows[1]["row_json"])["priority"] == "high"


def test_missing_column_fails_loudly():
    header = [c for c in sh.COLUMNS if c != "status"]
    with pytest.raises(ValueError, match="status"):
        sh.sheet_rows([header, ["LP-1"]])


def test_run_loads_snapshot_and_logs_success():
    sink = FakeSink()
    summary = sh.run_snapshot(session=FakeSession(seed_grid()), sink=sink)

    assert summary["status"] == "success" and summary["rows_loaded"] == 25
    loaded = sink.tables[sh.TABLE]
    assert all(r["_load_id"] == summary["run_id"] and r["_sheet_id"] == sh.SHEET_ID for r in loaded)
    assert sink.tables["ops.ingestion_log"][0]["source"] == "sheets"


def test_not_shared_gives_a_clear_failure():
    sink = FakeSink()
    summary = sh.run_snapshot(session=FakeSession(status=403), sink=sink)

    assert summary["status"] == "failed" and "share the sheet" in summary["error_message"]
    assert sink.tables["ops.ingestion_log"][0]["status"] == "failed"
