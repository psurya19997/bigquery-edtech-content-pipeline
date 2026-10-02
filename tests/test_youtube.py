"""YouTube function tests: fake API responses, no network, no BigQuery, no real key."""

import importlib.util
import json
from pathlib import Path

import pytest

# Loaded by path: every Cloud Run function folder has its own main.py.
_spec = importlib.util.spec_from_file_location(
    "youtube_main", Path(__file__).parents[1] / "functions" / "youtube" / "main.py"
)
yt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(yt)

CHANNEL_ID = "UCU0kWLAbhVGxXarmE3b8rHg"


def video(i: int) -> dict:
    return {
        "id": f"vid{i:03d}",
        "etag": f"e{i}",
        "snippet": {
            "channelId": CHANNEL_ID,
            "title": f"Video {i} [Hinglish] | Grade 12 | Math",
            "publishedAt": "2026-09-29T10:00:00Z",
            "tags": ["ncert"],
            "defaultAudioLanguage": "hi",
        },
        "contentDetails": {"duration": "PT12M34S", "caption": "true", "licensedContent": True},
        "statistics": {"viewCount": str(100 + i), "commentCount": "3"},  # likes hidden
        "status": {"privacyStatus": "public", "license": "youtube", "madeForKids": False},
    }


class FakeResponse:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status
        self.text = json.dumps(body)

    def json(self):
        return self.body


class FakeSession:
    """1 channel with 120 uploads: 3 playlist pages, 3 video batches."""

    def __init__(self, n_videos=120):
        self.n = n_videos
        self.calls = []

    def get(self, url, params=None, timeout=None):
        endpoint = url.rsplit("/", 1)[-1]
        self.calls.append(endpoint)
        if endpoint == "channels":
            return FakeResponse(
                {
                    "items": [
                        {
                            "id": CHANNEL_ID,
                            "snippet": {"title": "Khan Academy India"},
                            "statistics": {"viewCount": "5000", "subscriberCount": "1230000"},
                            "contentDetails": {"relatedPlaylists": {"uploads": "UU123"}},
                        }
                    ]
                }
            )
        if endpoint == "playlistItems":
            start = int(params.get("pageToken") or 0)
            ids = range(start, min(start + 50, self.n))
            body = {"items": [{"contentDetails": {"videoId": f"vid{i:03d}"}} for i in ids]}
            if start + 50 < self.n:
                body["nextPageToken"] = str(start + 50)
            return FakeResponse(body)
        if endpoint == "videos":
            ids = params["id"].split(",")
            return FakeResponse({"items": [video(int(i[3:])) for i in ids]})
        raise AssertionError(endpoint)


class FakeSink:
    def __init__(self, known=()):
        self.tables: dict[str, list] = {}
        self.known = set(known)

    def load(self, name, rows):
        self.tables.setdefault(name, []).extend(rows)

    def known_video_ids(self):
        return self.known


def test_duration_seconds():
    assert yt.duration_seconds("PT12M34S") == 754
    assert yt.duration_seconds("PT1H") == 3600
    assert yt.duration_seconds("P1DT2H3S") == 93603
    assert yt.duration_seconds(None) is None and yt.duration_seconds("garbage") is None


def test_video_row_flattens_fields_and_keeps_original():
    row = yt.video_row(video(7))
    assert row["duration_seconds"] == 754 and row["caption"] is True
    assert row["like_count"] is None and row["comment_count"] == 3
    assert row["default_audio_language"] == "hi" and row["tags"] == ["ncert"]
    assert json.loads(row["record_json"])["id"] == "vid007"


def test_snapshot_pages_through_uploads_and_batches_videos():
    session, sink = FakeSession(120), FakeSink()
    summary = yt.run_snapshot(session=session, sink=sink, key="fake")

    assert summary["status"] == "success" and summary["rows_loaded"] == 120
    assert session.calls.count("playlistItems") == 3 and session.calls.count("videos") == 3
    assert len(sink.tables["raw.youtube_video_stats_snapshot"]) == 120
    assert sink.tables["raw.youtube_channel_snapshot"][0]["subscriber_count"] == 1230000


def test_only_new_videos_get_detail_rows():
    sink = FakeSink(known={f"vid{i:03d}" for i in range(100)})
    yt.run_snapshot(session=FakeSession(120), sink=sink, key="fake")

    assert len(sink.tables["raw.youtube_videos"]) == 20
    assert len(sink.tables["raw.youtube_video_stats_snapshot"]) == 120  # stats for all


def test_details_are_flushed_in_chunks_not_all_at_once(monkeypatch):
    monkeypatch.setattr(yt, "DETAIL_CHUNK", 50)

    class CountingSink(FakeSink):
        def __init__(self):
            super().__init__()
            self.detail_loads = []

        def load(self, name, rows):
            if name == "raw.youtube_videos":
                self.detail_loads.append(len(rows))
            super().load(name, rows)

    sink = CountingSink()
    yt.run_snapshot(session=FakeSession(120), sink=sink, key="fake")

    assert sink.detail_loads == [50, 50, 20]  # never more than one chunk held in memory
    assert len(sink.tables["raw.youtube_videos"]) == 120


def test_refresh_details_rewrites_all_videos():
    sink = FakeSink(known={f"vid{i:03d}" for i in range(120)})
    yt.run_snapshot(session=FakeSession(120), sink=sink, key="fake", refresh_details=True)
    assert len(sink.tables["raw.youtube_videos"]) == 120


def test_missing_key_fails_and_is_logged(monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    sink = FakeSink()
    summary = yt.run_snapshot(session=FakeSession(), sink=sink)
    assert summary["status"] == "failed" and "YOUTUBE_API_KEY" in summary["error_message"]
    assert sink.tables["ops.ingestion_log"][0]["status"] == "failed"


def test_api_error_message_surfaces_without_retry():
    class QuotaSession(FakeSession):
        def get(self, url, params=None, timeout=None):
            self.calls.append("x")
            return FakeResponse({"error": {"message": "quota exceeded"}}, status=403)

    session = QuotaSession()
    with pytest.raises(yt.requests.HTTPError, match="quota exceeded"):
        yt.get_json(session, "channels", {}, "fake")
    assert len(session.calls) == 1  # 403 is not retried
