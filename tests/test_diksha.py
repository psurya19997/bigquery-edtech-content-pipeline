"""DIKSHA function tests: fake corpus and fake API, no network, no BigQuery."""

import json
from datetime import UTC, datetime, timedelta

import pytest

import main as diksha_fn
from main import content_row, fmt_ts, iter_slices, metrics_row, run_mode

START = datetime(2016, 1, 1, tzinfo=UTC)
END = datetime(2026, 10, 1, tzinfo=UTC)


def fake_count_fn(created: list[datetime], field: str = "createdOn"):
    """Count items whose timestamp falls in the filter's [>=, <) range, like DIKSHA does."""
    stamps = [fmt_ts(c) for c in created]
    calls = []

    def count(filters: dict) -> int:
        calls.append(filters)
        lo, hi = filters[field][">="], filters[field]["<"]
        return sum(lo <= s < hi for s in stamps)

    count.calls = calls
    return count


def spread(n: int, step: timedelta = timedelta(hours=1)) -> list[datetime]:
    return [START + i * step for i in range(n)]


# ------------------------------------------------------------------ slicing
def test_slices_fit_cap_and_cover_every_item_once():
    created = spread(25_000, step=timedelta(minutes=7))
    slices = list(iter_slices(fake_count_fn(created), {}, "createdOn", START, END, max_size=1_000))

    assert all(s.expected_count <= 1_000 for s in slices)
    assert sum(s.expected_count for s in slices) == len(created)
    assert not any(s.over_cap for s in slices)


def test_slices_are_oldest_first_and_non_overlapping():
    slices = list(iter_slices(fake_count_fn(spread(5_000)), {}, "createdOn", START, END, 300))
    ranges = [(s.filters["createdOn"][">="], s.filters["createdOn"]["<"]) for s in slices]

    for (_, prev_hi), (next_lo, _) in zip(ranges, ranges[1:], strict=False):
        assert prev_hi <= next_lo
    assert len({s.slice_id for s in slices}) == len(slices)


def test_empty_corpus_gives_no_slices():
    assert list(iter_slices(fake_count_fn([]), {}, "createdOn", START, END)) == []


def test_small_corpus_is_one_slice_from_one_count():
    count = fake_count_fn(spread(10))
    assert len(list(iter_slices(count, {}, "createdOn", START, END, max_size=100))) == 1
    assert len(count.calls) == 1


def test_burst_in_one_second_is_flagged_over_cap():
    burst = datetime(2020, 5, 1, 12, tzinfo=UTC)
    created = [burst] * 50 + spread(10)
    slices = list(iter_slices(fake_count_fn(created), {}, "createdOn", START, END, max_size=20))

    over = [s for s in slices if s.over_cap]
    assert len(over) == 1 and over[0].expected_count == 50
    assert sum(s.expected_count for s in slices) == len(created)


def test_base_filters_pass_through():
    count = fake_count_fn(spread(10))
    list(iter_slices(count, {"medium": ["Marathi"]}, "createdOn", START, END))
    assert all(c["medium"] == ["Marathi"] for c in count.calls)


def test_client_refuses_offsets_past_cap():
    with pytest.raises(ValueError):
        diksha_fn.Diksha().search({}, limit=1_000, offset=9_500)


# ------------------------------------------------------------------ rows
# Shaped like a real DIKSHA item (sparse: missing fields are omitted, metrics nested).
ITEM = {
    "identifier": "do_3126132210160271362631",
    "name": " 1.2 Video - Sounds of Letters",
    "board": "State (Maharashtra)",
    "gradeLevel": ["Class 1", "Class 2"],
    "medium": "Marathi",  # sometimes a plain string instead of a list
    "primaryCategory": "Teacher Resource",
    "createdOn": "2018-10-16T14:43:22.143+0000",
    "me_totalPlaySessionCount": {"portal": 126133},
    "me_averageRating": 3.79,
    "me_totalRatingsCount": 873,
}


def test_content_row_normalises_sparse_item():
    row = content_row(ITEM, "s1", "full", "load1", "2026-10-01T00:00:00+00:00")

    assert row["medium"] == ["Marathi"]
    assert row["subject"] == []  # omitted by DIKSHA -> empty array, not missing
    assert row["license"] is None
    assert json.loads(row["play_sessions_json"]) == {"portal": 126133}
    assert row["time_spent_json"] is None
    assert row["ratings_count"] == 873 and row["avg_rating"] == 3.79
    assert row["_mode"] == "full" and row["slice_id"] == "s1"


def test_metrics_row_has_only_ids_and_numbers():
    row = metrics_row(ITEM, "2026-10-01", "load1", "2026-10-01T00:00:00+00:00")
    assert row["identifier"] == ITEM["identifier"]
    assert row["snapshot_date"] == "2026-10-01"
    assert "name" not in row


# ------------------------------------------------------------------ run
class FakeDiksha:
    """Serves a fixed list of items, honouring createdOn ranges, offset and limit."""

    def __init__(self, n: int):
        self.items = [
            {"identifier": f"do_{i:05d}", "createdOn": fmt_ts(START + timedelta(days=i))}
            for i in range(n)
        ]

    def _match(self, filters):
        lo, hi = filters["createdOn"][">="], filters["createdOn"]["<"]
        return [i for i in self.items if lo <= i["createdOn"] < hi]

    def count(self, filters):
        return len(self._match(filters))

    def search(self, filters, *, limit=0, offset=0, fields=None, sort_by=None):
        return {"content": self._match(filters)[offset : offset + limit]}


class FakeSink:
    def __init__(self):
        self.tables: dict[str, list] = {}

    def load(self, name, rows):
        self.tables.setdefault(name, []).extend(rows)


def test_full_run_loads_every_item_and_logs_success():
    sink = FakeSink()
    summary = run_mode("full", diksha=FakeDiksha(2_500), sink=sink)

    assert summary["status"] == "success"
    assert summary["rows_loaded"] == 2_500
    assert len({r["identifier"] for r in sink.tables["raw.diksha_content"]}) == 2_500
    assert sink.tables["ops.ingestion_log"][0]["status"] == "success"


def test_limited_run_is_partial_so_it_never_becomes_a_watermark():
    sink = FakeSink()
    summary = run_mode("metrics", limit=30, diksha=FakeDiksha(100), sink=sink)

    assert summary["status"] == "partial"
    assert len(sink.tables["raw.diksha_metrics_snapshot"]) == 30


def test_failure_is_logged_not_raised():
    class Broken(FakeDiksha):
        def count(self, filters):
            raise RuntimeError("DIKSHA down")

    sink = FakeSink()
    summary = run_mode("full", diksha=Broken(1), sink=sink)

    assert summary["status"] == "failed" and "DIKSHA down" in summary["error_message"]
    assert sink.tables["ops.ingestion_log"][0]["status"] == "failed"
