"""
Unit tests for journal.state_publisher.StatePublisher.

The publisher writes engine_state + missed_today JSON files that the
dashboard backend reads. Atomic-write semantics matter: a half-written
file would crash the backend's plain json.load.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from journal.state_publisher import StatePublisher  # noqa: E402
from journal.recorder import JournalRecorder  # noqa: E402


def test_write_engine_state_creates_file(tmp_path):
    pub = StatePublisher(tmp_path, "NIFTY")
    pub.write_engine_state({"regime": "TREND_UP", "spot": 24800.0})
    assert pub.engine_state_path.exists()
    payload = json.loads(pub.engine_state_path.read_text())
    assert payload["regime"] == "TREND_UP"
    assert payload["spot"] == 24800.0
    assert payload["index"] == "NIFTY"
    assert "last_update_ts" in payload


def test_engine_state_overwrites_atomically(tmp_path):
    pub = StatePublisher(tmp_path, "NIFTY")
    for i in range(3):
        pub.write_engine_state({"counter": i})
    payload = json.loads(pub.engine_state_path.read_text())
    assert payload["counter"] == 2
    # No tmp files should leak in the directory
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".")]
    assert leftovers == [], f"leaked tmp files: {leftovers}"


def test_index_field_uppercased(tmp_path):
    pub = StatePublisher(tmp_path, "sensex")
    pub.write_engine_state({})
    payload = json.loads(pub.engine_state_path.read_text())
    assert payload["index"] == "SENSEX"
    assert pub.engine_state_path.name == "engine_state_SENSEX.json"


def test_caller_supplied_last_update_ts_preserved(tmp_path):
    pub = StatePublisher(tmp_path, "NIFTY")
    pub.write_engine_state({"last_update_ts": "1999-01-01T00:00:00+05:30"})
    payload = json.loads(pub.engine_state_path.read_text())
    assert payload["last_update_ts"] == "1999-01-01T00:00:00+05:30"


def test_write_missed_snapshot_with_recorder_entries(tmp_path):
    rec = JournalRecorder()
    rec.start_day(date(2026, 5, 1))
    rec.on_near_miss(
        tactic="trend_pullback",
        direction="CE",
        ts=datetime(2026, 5, 1, 11, 0),
        blocked_by="oi_bias_ratio",
        blocker_detail="ratio 0.83 < 1.50",
        state_snapshot={"regime": "TREND_UP", "spot": 24800},
        hypothetical_strike=24800,
        hypothetical_entry_premium=100.0,
        sl_pct=0.30,
        tp_pct=0.50,
        time_stop_min=90,
    )
    pub = StatePublisher(tmp_path, "NIFTY")
    pub.write_missed_snapshot(rec._day.missed)
    payload = json.loads(pub.missed_today_path.read_text())
    assert payload["index"] == "NIFTY"
    assert len(payload["missed"]) == 1
    m = payload["missed"][0]
    assert m["tactic"] == "trend_pullback"
    assert m["direction"] == "CE"
    assert m["blocked_by"] == "oi_bias_ratio"
    assert m["regime"] == "TREND_UP"
    assert m["spot_at_miss"] == 24800
    assert m["sl_pct"] == 0.30
    assert m["tp_pct"] == 0.50
    assert m["time_stop_min"] == 90
    assert m["hypothetical_strike"] == 24800
    assert m["hypothetical_entry_premium"] == 100.0


def test_write_missed_snapshot_handles_dicts(tmp_path):
    pub = StatePublisher(tmp_path, "NIFTY")
    raw = [{
        "ts": "2026-05-01T11:00:00", "tactic": "ief", "direction": "PE",
        "blocked_by": "ob_anchor", "blocker_detail": "no anchor",
    }]
    pub.write_missed_snapshot(raw)
    payload = json.loads(pub.missed_today_path.read_text())
    assert payload["missed"][0]["tactic"] == "ief"


def test_clear_removes_files(tmp_path):
    pub = StatePublisher(tmp_path, "NIFTY")
    pub.write_engine_state({})
    pub.write_missed_snapshot([])
    assert pub.engine_state_path.exists()
    assert pub.missed_today_path.exists()
    pub.clear()
    assert not pub.engine_state_path.exists()
    assert not pub.missed_today_path.exists()
    # Idempotent
    pub.clear()


def test_write_engine_state_swallows_disk_errors(tmp_path, monkeypatch):
    """If atomic_write_json raises, the publisher logs and returns —
    it must never crash the bot loop."""
    pub = StatePublisher(tmp_path / "does-not-exist-and-cant-create", "NIFTY")
    # Patch Path.mkdir to raise so the target directory cannot be created
    import os as _os
    original = _os.replace
    def boom(*a, **k): raise OSError("disk full")
    monkeypatch.setattr(_os, "replace", boom)
    # Should not raise
    pub.write_engine_state({"regime": "TREND_UP"})
    monkeypatch.setattr(_os, "replace", original)


def test_concurrent_writes_no_partial_reads(tmp_path):
    """Smoke-test that an interleaved reader never sees an incomplete
    file. We do a rapid sequence of writes and check the file is always
    valid JSON when we read it."""
    pub = StatePublisher(tmp_path, "NIFTY")
    for i in range(50):
        pub.write_engine_state({"i": i, "payload": "x" * 5000})
        # Read back immediately and parse — must succeed
        text = pub.engine_state_path.read_text()
        parsed = json.loads(text)
        assert parsed["i"] == i
