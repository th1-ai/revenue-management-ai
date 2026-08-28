"""tools/sync_book.py - pull capacity + on-the-books from the PMS into our own
``nights`` table, and load it back out as plain `pricing_engine.Night` rows.

This is the only module in this agent that talks to `core.adapters.get_pms()`.
Everything downstream (`tools/pricing_engine.py`, `tools/forecast_engine.py`)
works on the `Night` dataclass and never imports an adapter.

``nights`` is this repo's own table (``core.store.migrate()``) - not one of
the shared `items`/`runs`/`events` tables. It holds capacity and OTB (read
fresh from the PMS every sync) plus `rate_override`/`mlos`/`mlos_override`
(our own published state, never touched by a sync - see
docs/how-it-works.md "Idempotency").

``--dry-run`` writes nothing to ``data/agent.db`` - not even the capacity/OTB
cache. `sync_nights()` is a no-op when `settings.dry_run`; `load_nights()`
computes the same rows live from the PMS instead, so a rehearsal still sees
real numbers, it just never persists them.
"""

from __future__ import annotations

from datetime import date, timedelta

from core.config import Settings
from core.store import Store, utcnow
from tools import ingest
from tools.pricing_engine import Night

SCHEMA = """
CREATE TABLE IF NOT EXISTS nights (
  date            TEXT NOT NULL,
  room_type_id    TEXT NOT NULL,
  room_type_name  TEXT,
  capacity        INTEGER NOT NULL DEFAULT 0,
  otb_rooms       INTEGER NOT NULL DEFAULT 0,
  pace_vs_ly_pts  REAL NOT NULL DEFAULT 0,
  rate_override   REAL,
  mlos            INTEGER NOT NULL DEFAULT 1,
  mlos_override   INTEGER,
  updated_at      TEXT NOT NULL,
  PRIMARY KEY (date, room_type_id)
);
"""

CANCELLED_STATUSES = {"cancelled", "canceled", "no_show"}


def migrate(store: Store) -> None:
    store.migrate(SCHEMA)


def date_range(today: str, horizon_nights: int) -> list[str]:
    start = date.fromisoformat(today)
    return [(start + timedelta(days=i)).isoformat() for i in range(horizon_nights)]


def _read_capacity_and_otb(settings: Settings, dates: list[str]):
    """Pure PMS reads - no store, no write. Shared by sync_nights and the
    dry-run path in load_nights."""
    from core.adapters import get_pms

    pms = get_pms(settings)
    room_types = {rt.id: rt for rt in pms.list_room_types() if rt.id}
    reservations = pms.list_reservations(dates[0], dates[-1])
    otb: dict[tuple[str, str], int] = {}
    for res in reservations:
        if res.status in CANCELLED_STATUSES or not res.room_type_id:
            continue
        d = res.check_in
        while d < res.check_out:
            if dates[0] <= d <= dates[-1]:
                otb[(d, res.room_type_id)] = otb.get((d, res.room_type_id), 0) + 1
            d = (date.fromisoformat(d) + timedelta(days=1)).isoformat()
    return room_types, otb


def sync_nights(settings: Settings, store: Store, *, horizon_nights: int = 21,
               today: str | None = None) -> int:
    """Refresh capacity + OTB for every (date, room type) in the horizon.

    A no-op under `--dry-run`: returns 0 and writes nothing. Never touches
    `rate_override`, `mlos` or `mlos_override` - those are only ever changed
    by an approved, sent proposal.
    """
    migrate(store)
    if settings.dry_run:
        return 0
    today = today or date.today().isoformat()
    dates = date_range(today, horizon_nights)
    room_types, otb = _read_capacity_and_otb(settings, dates)
    pace = ingest.load_pace()
    now = utcnow()
    written = 0
    for day in dates:
        for rt_id, rt in room_types.items():
            store.db.execute(
                "INSERT INTO nights (date, room_type_id, room_type_name, capacity, "
                "otb_rooms, pace_vs_ly_pts, mlos, updated_at) VALUES (?,?,?,?,?,?,1,?) "
                "ON CONFLICT(date, room_type_id) DO UPDATE SET "
                "room_type_name=excluded.room_type_name, capacity=excluded.capacity, "
                "otb_rooms=excluded.otb_rooms, pace_vs_ly_pts=excluded.pace_vs_ly_pts, "
                "updated_at=excluded.updated_at",
                (day, rt_id, rt.name, rt.count, otb.get((day, rt_id), 0),
                 pace.get((day, rt_id), 0.0), now))
            written += 1
    return written


def load_nights(settings: Settings, store: Store, *, today: str | None = None,
                horizon_nights: int = 21) -> list[Night]:
    """Read the `nights` table back out as plain dataclasses for the engine.

    Under `--dry-run` (nothing was synced above), capacity/OTB/pace are
    computed live from the PMS instead, and any *existing* override from an
    earlier real run is overlaid with a read-only ``SELECT`` - a rehearsal
    still reflects real published state, it just cannot create any.
    """
    migrate(store)
    today = today or date.today().isoformat()
    dates = date_range(today, horizon_nights)
    if settings.dry_run:
        room_types, otb = _read_capacity_and_otb(settings, dates)
        pace = ingest.load_pace()
        existing = {(r["date"], r["room_type_id"]): r for r in store.db.execute(
            "SELECT * FROM nights WHERE date BETWEEN ? AND ?", (dates[0], dates[-1])
        ).fetchall()}
        out = []
        for day in dates:
            for rt_id, rt in room_types.items():
                ex = existing.get((day, rt_id))
                out.append(Night(
                    date=day, room_type_id=rt_id, capacity=rt.count,
                    otb_rooms=otb.get((day, rt_id), 0), pace_vs_ly_pts=pace.get((day, rt_id), 0.0),
                    rate_override=ex["rate_override"] if ex else None,
                    mlos=ex["mlos"] if ex else 1,
                    mlos_override=ex["mlos_override"] if ex else None))
        return out
    rows = store.db.execute(
        "SELECT * FROM nights WHERE date BETWEEN ? AND ? ORDER BY date, room_type_id",
        (dates[0], dates[-1])).fetchall()
    return [Night(date=r["date"], room_type_id=r["room_type_id"], capacity=r["capacity"],
                  otb_rooms=r["otb_rooms"], pace_vs_ly_pts=r["pace_vs_ly_pts"],
                  rate_override=r["rate_override"], mlos=r["mlos"],
                  mlos_override=r["mlos_override"]) for r in rows]


def publish_rate(store: Store, day: str, room_type_id: str, price: float) -> None:
    """Record a published rate on our own `nights` row (call after `pms.set_rate()`).

    Never call this under `--dry-run` - the write guard already blocked the
    real `pms.set_rate()` call before this would run.
    """
    migrate(store)
    store.db.execute(
        "UPDATE nights SET rate_override=?, updated_at=? WHERE date=? AND room_type_id=?",
        (price, utcnow(), day, room_type_id))


def publish_mlos(store: Store, day: str, mlos: int) -> None:
    """Record a published stay-rule change on every room type for that date."""
    migrate(store)
    store.db.execute(
        "UPDATE nights SET mlos_override=?, updated_at=? WHERE date=?",
        (mlos, utcnow(), day))
