"""
StatePublisher — atomic JSON writers used by the live bot to expose
its in-memory state to the dashboard backend (which is a separate
process). The backend reads these files via plain `open()` so we
must write atomically (write tmp file, then os.rename) to avoid
races where the reader sees a half-written file.

Two outputs per index, both in the project-level data/ directory:

    data/engine_state_<INDEX>.json   — current regime, spot, signal, etc.
    data/missed_today_<INDEX>.json   — list of today's near-misses

Both files are tiny (a few KB), so re-writing them every main-loop
iteration is fine. Bigger picture: this is a pull-style IPC. The
backend has zero coupling to the bot beyond agreeing on the file paths
and JSON shape.

All public methods swallow exceptions (logged, never raised) so a disk
hiccup never knocks out trading.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("state_publisher")


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Write JSON to a temp file in the same directory, then os.rename
    onto the target. rename(2) is atomic on POSIX; on Windows os.replace
    is atomic too. This guarantees the reader either sees the previous
    contents or the new full contents — never a partial write."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, default=str, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class StatePublisher:
    """Writes engine_state and missed_today JSON for one trading index.

    Args:
      out_dir: usually `<repo>/data`.
      index_str: "NIFTY" or "SENSEX" — used as the filename suffix so
        both bots can run side-by-side without collisions.
    """

    def __init__(self, out_dir: Path, index_str: str):
        self.out_dir = Path(out_dir)
        self.index = index_str.upper()
        self._engine_path = self.out_dir / f"engine_state_{self.index}.json"
        self._missed_path = self.out_dir / f"missed_today_{self.index}.json"

    @property
    def engine_state_path(self) -> Path:
        return self._engine_path

    @property
    def missed_today_path(self) -> Path:
        return self._missed_path

    # ----- engine state ------------------------------------------------

    def write_engine_state(self, state: dict) -> None:
        """Overwrite the engine_state file with `state`. A `last_update_ts`
        and `index` field are auto-added if not present."""
        try:
            payload = dict(state)
            payload.setdefault(
                "last_update_ts", datetime.now().astimezone().isoformat(),
            )
            payload.setdefault("index", self.index)
            _atomic_write_json(self._engine_path, payload)
        except Exception as e:
            log.debug("write_engine_state failed: %s", e)

    # ----- missed-today snapshot ---------------------------------------

    def write_missed_snapshot(self, missed_list: list) -> None:
        """Overwrite the missed_today file with the given list. The bot
        passes the recorder's in-memory list each iteration so the file
        always reflects the latest state (registered + finalised)."""
        try:
            payload = {
                "last_update_ts": datetime.now().astimezone().isoformat(),
                "index": self.index,
                "missed": [_serialize_missed(m) for m in missed_list],
            }
            _atomic_write_json(self._missed_path, payload)
        except Exception as e:
            log.debug("write_missed_snapshot failed: %s", e)

    # ----- bookkeeping -------------------------------------------------

    def clear(self) -> None:
        """Remove both files (used at end of trading day so stale state
        doesn't linger to the next session)."""
        for p in (self._engine_path, self._missed_path):
            try:
                if p.exists():
                    p.unlink()
            except OSError as e:
                log.debug("clear failed for %s: %s", p, e)


def _serialize_missed(m: Any) -> dict:
    """Convert a journal.models.MissedEntry (or a plain dict) into a
    JSON-friendly dict."""
    if isinstance(m, dict):
        return m
    out = {}
    for attr in (
        "ts", "tactic", "direction", "blocked_by", "blocker_detail",
        "hypothetical_strike", "hypothetical_entry_premium",
        "hypothetical_exit_premium", "hypothetical_pnl",
        "hypothetical_outcome", "hypothetical_explanation",
        "sl_pct", "tp_pct", "time_stop_min", "poll_count",
    ):
        v = getattr(m, attr, None)
        if isinstance(v, datetime):
            out[attr] = v.isoformat()
        else:
            out[attr] = v
    state = getattr(m, "state_snapshot", None) or {}
    out["regime"] = state.get("regime", "")
    out["spot_at_miss"] = state.get("spot", "")
    return out
