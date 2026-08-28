"""tools/ingest.py - the signals no PMS exposes: pace, comp rates, events/weather,
OTA-observed rates, OTA content findings.

No adapter in `core/adapters/` covers a rate shopper, an events feed or an OTA
extranet (see docs/how-it-works.md "Design decisions" #1). Every function here
reads the same shape two ways:

- `data/imports/<name>.csv`   your own export, or a script you point at a
                               rate-shopping / events tool. Checked first.
- `fixtures/inbound/<name>.json`   the bundled sample data `make demo` and the
                               tests run on. Used when the CSV is absent.

Both return the same list of dataclasses either way, so `tools/pricing_engine.py`
never knows or cares which one supplied them.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from core.config import repo_root
from tools.pricing_engine import CompRate, Event

IMPORTS_DIR = repo_root() / "data" / "imports"
INBOUND_DIR = repo_root() / "fixtures" / "inbound"


def _read_csv(name: str) -> list[dict] | None:
    path = IMPORTS_DIR / f"{name}.csv"
    if not path.exists():
        return None
    with path.open(newline="", encoding="utf-8") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def _read_json(name: str) -> list[dict]:
    path = INBOUND_DIR / f"{name}.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else data.get(name, [])


def _rows(name: str) -> list[dict]:
    """CSV first (your own data), fixtures second (the demo)."""
    rows = _read_csv(name)
    return rows if rows is not None else _read_json(name)


def load_pace(name: str = "pace") -> dict[tuple[str, str], float]:
    """``{(date, room_type_id): pace_vs_ly_pts}``. Missing cells default to 0 (neutral)."""
    out: dict[tuple[str, str], float] = {}
    for row in _rows(name):
        date = str(row.get("date", "")).strip()
        rt = str(row.get("room_type_id", "")).strip()
        if not date or not rt:
            continue
        out[(date, rt)] = float(row.get("pace_vs_ly_pts", 0) or 0)
    return out


def load_comp_rates(name: str = "comp_rates") -> list[CompRate]:
    out = []
    for row in _rows(name):
        rt = str(row.get("room_type_id") or "").strip() or None
        out.append(CompRate(
            competitor=str(row.get("competitor", "")).strip(),
            date=str(row.get("date", "")).strip(),
            rate_multiplier=float(row.get("rate_multiplier", 1.0) or 1.0),
            room_type_id=rt, note=str(row.get("note") or "")))
    return out


def load_events(name: str = "events") -> list[Event]:
    out = []
    for row in _rows(name):
        out.append(Event(
            name=str(row.get("name", "")).strip(),
            kind=str(row.get("kind", "")).strip(),
            category=str(row.get("category", "event")).strip(),
            start_date=str(row.get("start_date", "")).strip(),
            end_date=str(row.get("end_date") or row.get("start_date", "")).strip(),
            note=str(row.get("note") or "")))
    return out


def load_ota_rates(name: str = "ota_rates") -> list[dict[str, Any]]:
    """``[{channel, date, room_type_id, observed_rate}]`` - the Cartographer's parity input."""
    out = []
    for row in _rows(name):
        out.append({
            "channel": str(row.get("channel", "")).strip(),
            "date": str(row.get("date", "")).strip(),
            "room_type_id": str(row.get("room_type_id", "")).strip(),
            "observed_rate": float(row.get("observed_rate", 0) or 0),
        })
    return out


def load_ota_content_findings(name: str = "ota_content_findings") -> list[dict[str, Any]]:
    """``[{channel, kind, detail, severity}]`` - fed to the Cartographer's scorer."""
    out = []
    for row in _rows(name):
        out.append({
            "channel": str(row.get("channel", "")).strip(),
            "kind": str(row.get("kind", "")).strip(),
            "detail": str(row.get("detail", "")).strip(),
            "severity": str(row.get("severity", "medium")).strip() or "medium",
        })
    return out


def sources_used() -> dict[str, str]:
    """Which source (csv/fixture) each signal is actually reading from - for doctor."""
    out = {}
    for name in ("pace", "comp_rates", "events", "ota_rates", "ota_content_findings"):
        if (IMPORTS_DIR / f"{name}.csv").exists():
            out[name] = f"data/imports/{name}.csv"
        elif (INBOUND_DIR / f"{name}.json").exists():
            out[name] = f"fixtures/inbound/{name}.json (demo data)"
        else:
            out[name] = "none - defaults to empty/neutral"
    return out
