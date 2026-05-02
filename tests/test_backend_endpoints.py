"""
Smoke tests for the backend FastAPI endpoints that read on-disk IPC
files written by main.py. Uses fastapi.TestClient to call the live
app without spinning up uvicorn.

Skipped automatically when fastapi is not installed in the current
environment (e.g. in a stripped-down sandbox). On the production env
that runs the dashboard, fastapi is required and these will run.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

fastapi = pytest.importorskip("fastapi")
psutil = pytest.importorskip("psutil")
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def app(monkeypatch, tmp_path):
    """Import backend.server with BASE_DIR redirected to a tmp_path so
    portfolio / engine_state files can be staged per-test. We also
    monkeypatch the indices cache to return a deterministic fixed list."""
    # Ensure import side-effects don't reach real disk
    monkeypatch.setenv("UPSTOX_API_KEY", "test")
    monkeypatch.setenv("UPSTOX_API_SECRET", "test")
    monkeypatch.setenv("UPSTOX_REDIRECT_URI", "http://localhost/cb")

    # Pre-create the data subdir
    (tmp_path / "data").mkdir()
    (tmp_path / "state").mkdir()
    (tmp_path / "logs").mkdir()

    import backend.server as srv
    monkeypatch.setattr(srv, "BASE_DIR", tmp_path)
    monkeypatch.setattr(srv, "PORTFOLIO_FILE", tmp_path / "data" / "paper_portfolio.json")
    monkeypatch.setattr(srv, "CONFIG_FILE", tmp_path / "project_config.json")
    monkeypatch.setattr(srv, "SESSION_FILE", tmp_path / "state" / "upstox_session.json")
    monkeypatch.setattr(srv, "LOG_DIR", tmp_path / "logs")

    # Stub the indices cache
    class StubCache:
        def get_all(self):
            return [{
                "symbol": "NIFTY 50",
                "instrument_key": "NSE_INDEX|Nifty 50",
                "ltp": 24800.50, "prev_close": 24750.0,
                "change": 50.50, "change_pct": 0.204,
                "ts": "2026-05-01T11:00:00+05:30", "stale": False,
            }]
    monkeypatch.setattr(srv, "indices_cache", StubCache())
    return srv, tmp_path


def test_quotes_indices(app):
    srv, _ = app
    client = TestClient(srv.app)
    r = client.get("/quotes/indices")
    assert r.status_code == 200
    body = r.json()
    assert "quotes" in body
    assert body["quotes"][0]["symbol"] == "NIFTY 50"
    assert body["quotes"][0]["ltp"] == 24800.50


def test_engine_state_unavailable_when_no_file(app):
    srv, _ = app
    client = TestClient(srv.app)
    r = client.get("/engine/state/NIFTY")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["bot_type"] == "NIFTY"


def test_engine_state_reads_published_file(app):
    srv, tmp = app
    state = {
        "regime": "TREND_UP", "spot": 24800.0, "vwap": 24795.0,
        "in_position": False, "last_signal": None,
    }
    (tmp / "data" / "engine_state_NIFTY.json").write_text(json.dumps(state))
    client = TestClient(srv.app)
    r = client.get("/engine/state/NIFTY")
    body = r.json()
    assert body["available"] is True
    assert body["regime"] == "TREND_UP"
    assert body["spot"] == 24800.0


def test_near_miss_today_reads_file(app):
    srv, tmp = app
    payload = {"missed": [{"tactic": "trend_pullback", "direction": "CE"}]}
    (tmp / "data" / "missed_today_NIFTY.json").write_text(json.dumps(payload))
    client = TestClient(srv.app)
    r = client.get("/near-miss/today/NIFTY")
    body = r.json()
    assert body["available"] is True
    assert len(body["missed"]) == 1


def test_pnl_today_filters_to_today(app):
    srv, tmp = app
    today_iso = datetime.now().date().isoformat()
    portfolio = {
        "capital": 102000.0,
        "open_position": None,
        "trade_history": [
            {"exit_time": f"{today_iso}T11:30:00", "pnl": 1500.0},
            {"exit_time": f"{today_iso}T13:30:00", "pnl": 500.0},
            {"exit_time": "2025-01-01T11:30:00", "pnl": -999.0},  # ignored
        ],
    }
    (tmp / "data" / "paper_portfolio_NIFTY.json").write_text(json.dumps(portfolio))
    client = TestClient(srv.app)
    r = client.get("/pnl/today/NIFTY")
    body = r.json()
    assert body["realized_pnl"] == 2000.0
    assert body["trade_count"] == 2
    assert body["win_count"] == 2
    assert body["loss_count"] == 0
    assert body["pnl_timeseries"][-1]["cumulative_pnl"] == 2000.0


def test_pnl_today_returns_defaults_for_missing_portfolio(app):
    srv, _ = app
    client = TestClient(srv.app)
    r = client.get("/pnl/today/NIFTY")
    body = r.json()
    assert body["realized_pnl"] == 0.0
    assert body["trade_count"] == 0
    assert body["pnl_timeseries"] == []


def test_health_still_works(app):
    srv, _ = app
    client = TestClient(srv.app)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
