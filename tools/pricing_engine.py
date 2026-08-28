"""tools/pricing_engine.py - the Quant's whole decision engine. Pure functions.

No I/O anywhere in this file: every function takes plain dataclasses in and
returns plain dataclasses out. `tools/run.py` is the only place that talks to
the PMS, the store, or an LLM. This split is what lets `tools/demo.py` and
every test in `tests/test_pricing_engine.py` exercise the exact same code
path a real overnight run does.

Every proposal is priced off the FORMULA rate, never off an existing
`rate_override` - otherwise a re-run would compound its own moves. See
docs/how-it-works.md "The house rate formula".
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

CURVE_POINTS = 31
CURVE_SPAN = 0.30
CURVE_K = 9


# --------------------------------------------------------------------------
# plain data
# --------------------------------------------------------------------------
@dataclass
class RoomTypeCfg:
    id: str
    name: str
    base_rate: float
    floor: float
    ceiling: float


@dataclass
class Night:
    """One (date, room type) cell of the live book."""

    date: str
    room_type_id: str
    capacity: int
    otb_rooms: int
    pace_vs_ly_pts: float = 0.0
    rate_override: float | None = None
    mlos: int = 1
    mlos_override: int | None = None

    @property
    def occupancy_pct(self) -> float:
        return round(100 * self.otb_rooms / self.capacity, 1) if self.capacity else 0.0

    @property
    def current_mlos(self) -> int:
        return self.mlos_override if self.mlos_override is not None else self.mlos


@dataclass
class CompRate:
    """One rival's published rate for one night."""

    competitor: str
    date: str
    rate_multiplier: float
    room_type_id: str | None = None  # None = applies to every room type
    note: str = ""


@dataclass
class Event:
    """One local event or weather front, in the shared feed."""

    name: str
    kind: str  # congress | regatta | low_demand | rain | sunny
    category: str  # event | weather
    start_date: str
    end_date: str
    note: str = ""

    def covers(self, day: str) -> bool:
        return self.start_date <= day <= self.end_date


@dataclass
class Proposal:
    """One thing the engine wants to change. The contract a review shows."""

    date: str
    kind: str  # rate | gap_night | mlos | offer
    room_type_id: str | None = None
    current: float | None = None
    proposed: float | None = None
    formula: float | None = None  # the baseline this was priced off - see price_response_curve
    current_mlos: int | None = None
    proposed_mlos: int | None = None
    delta_pct: float | None = None
    reason: str = ""
    parts: list[dict] = field(default_factory=list)
    auto: bool = False
    hold_reason: str = ""

    def unique_key(self, run_date: str) -> str:
        if self.room_type_id:
            return f"{run_date}:{self.date}:{self.room_type_id}"
        return f"{run_date}:{self.date}"


@dataclass
class RepricingResult:
    proposals: list[Proposal]
    thinking_log: list[str]
    summary: dict[str, Any]


# --------------------------------------------------------------------------
# the house rate formula - deterministic, shared by every mode
# --------------------------------------------------------------------------
def round_to(value: float, step: int) -> float:
    return round(value / step) * step


def formula_rate(room_type: RoomTypeCfg, day: str, cfg: dict) -> float:
    """The baseline every proposal is priced off. Never call this on an override."""
    d = date.fromisoformat(day)
    season = cfg["season_multiplier"][d.month - 1]
    weekend = cfg.get("weekend_multiplier", 1.08) if d.weekday() in (4, 5) else 1.0
    return round_to(room_type.base_rate * season * weekend, cfg.get("round_to", 5))


def current_rate(night: Night, formula: float) -> float:
    """What the guest pays today: the override if one exists, else the formula."""
    return night.rate_override if night.rate_override is not None else formula


def room_types_from_cfg(cfg: dict) -> dict[str, "RoomTypeCfg"]:
    """``config/agent.yaml: room_types`` is a mapping keyed by your own room type
    id: ``{id: {name, base_rate, floor, ceiling}}``. This is the one place that
    shape is read - everything else works with :class:`RoomTypeCfg`."""
    raw = cfg.get("room_types") or {}
    return {rt_id: RoomTypeCfg(id=rt_id, **(fields or {})) for rt_id, fields in raw.items()}


class SimpleEngineConfigError(ValueError):
    """``engine: simple`` needs ``simple.reference_room_type`` to name one of
    your ``room_types`` - the whole simple-mode counter-offer is priced off
    that one room. A hotel that customises ``room_types`` (workflows/00-setup.md
    step 3) without also updating this value used to hit a bare ``KeyError``
    here; this is the readable message instead. Raised by
    :func:`simple_reference_room_type`, called from both ``run_simple_pricing``
    below and ``tools/doctor.py:check_simple_engine`` so a hotel finds out from
    `make doctor` before `make run` ever gets a chance to fail.
    """


def simple_reference_room_type(cfg: dict) -> str:
    """Return the validated ``simple.reference_room_type`` id, or raise
    :class:`SimpleEngineConfigError` naming the bad value and the valid
    options - one readable line, never a bare ``KeyError``."""
    room_types = room_types_from_cfg(cfg)
    ref_id = (cfg.get("simple") or {}).get("reference_room_type")
    if ref_id in room_types:
        return ref_id
    valid = ", ".join(sorted(room_types)) or "(no room_types configured)"
    raise SimpleEngineConfigError(
        f"config/agent.yaml: engine: simple needs simple.reference_room_type to "
        f"name one of your room_types - got {ref_id!r}, valid options: {valid}. "
        f"See workflows/00-setup.md step 3.")


# --------------------------------------------------------------------------
# comp-set reading
# --------------------------------------------------------------------------
def comp_median_multiplier(comps: list[CompRate], day: str,
                           room_type_id: str | None = None) -> float:
    """Median rival rate_multiplier for one night. 1.0 (neutral) with no data."""
    values = [c.rate_multiplier for c in comps
              if c.date == day and (c.room_type_id in (None, room_type_id))]
    return round(statistics.median(values), 3) if values else 1.0


# --------------------------------------------------------------------------
# event radar
# --------------------------------------------------------------------------
UPLIFT_KINDS = {"congress", "regatta"}


def uplift_event_for(events: list[Event], day: str) -> Event | None:
    return next((e for e in events if e.category == "event"
                and e.kind in UPLIFT_KINDS and e.covers(day)), None)


def low_demand_event_for(events: list[Event], day: str) -> Event | None:
    return next((e for e in events if e.category == "event"
                and e.kind == "low_demand" and e.covers(day)), None)


# --------------------------------------------------------------------------
# gap nights - step 5
# --------------------------------------------------------------------------
def is_gap_night(by_date: dict[str, list[Night]], day: str, cfg: dict) -> bool:
    """Both neighbours full, this night trailing both by gap_drop_pts."""
    d = date.fromisoformat(day)
    prev_rows = by_date.get((d - timedelta(days=1)).isoformat())
    next_rows = by_date.get((d + timedelta(days=1)).isoformat())
    this_rows = by_date.get(day)
    if not (prev_rows and next_rows and this_rows):
        return False
    prev_occ = max(r.occupancy_pct for r in prev_rows)
    next_occ = max(r.occupancy_pct for r in next_rows)
    this_occ = max(r.occupancy_pct for r in this_rows)
    neighbour_floor = cfg.get("gap_neighbour_occ_pct", 85)
    drop = cfg.get("gap_drop_pts", 25)
    return (prev_occ >= neighbour_floor and next_occ >= neighbour_floor
            and this_occ <= prev_occ - drop and this_occ <= next_occ - drop)


# --------------------------------------------------------------------------
# stay rules (MLOS) - step 6, date-level (see docs/how-it-works.md #6)
# --------------------------------------------------------------------------
def mlos_proposal(day: str, rows: list[Night], events: list[Event], rules: dict,
                  cfg: dict) -> Proposal | None:
    if not rules.get("mlos_guard", True):
        return None
    occ = date_occupancy_pct(rows)
    current = rows[0].current_mlos
    d = date.fromisoformat(day)
    is_weekend = d.weekday() in (4, 5)
    uplift = uplift_event_for(events, day) if rules.get("event_radar", True) else None
    peak = (uplift is not None and occ >= cfg.get("mlos_event_occ_pct", 80)) or \
        (is_weekend and occ >= cfg.get("mlos_weekend_occ_pct", 90))
    if peak and current < 2:
        why = uplift.name if uplift else "a busy weekend"
        return Proposal(date=day, kind="mlos", current_mlos=current, proposed_mlos=2,
                        reason=f"Set minimum stay 2 nights - one-night bookings would "
                               f"block longer guests on a peak night. ({why}, "
                               f"{occ:.0f}% on the books)")
    if current >= 2 and occ < cfg.get("mlos_release_occ_pct", 60):
        return Proposal(date=day, kind="mlos", current_mlos=current, proposed_mlos=1,
                        reason=f"Clear the {current}-night minimum - demand has faded "
                               f"({occ:.0f}% on the books) - release the stay "
                               f"restriction so single nights can book.")
    return None


# --------------------------------------------------------------------------
# per-cell aggregates
# --------------------------------------------------------------------------
def _by_date(nights: list[Night]) -> dict[str, list[Night]]:
    out: dict[str, list[Night]] = {}
    for n in nights:
        out.setdefault(n.date, []).append(n)
    return out


def date_occupancy_pct(rows: list[Night]) -> float:
    cap = sum(r.capacity for r in rows)
    otb = sum(r.otb_rooms for r in rows)
    return round(100 * otb / cap, 1) if cap else 0.0


def date_pace_pts(rows: list[Night]) -> float:
    return round(sum(r.pace_vs_ly_pts for r in rows) / len(rows), 1) if rows else 0.0


# --------------------------------------------------------------------------
# step 7 - draft one cell's proposal
# --------------------------------------------------------------------------
def _clamp_and_finish(room_type: RoomTypeCfg, formula: float, raw: float, current: float,
                      rules: dict, exempt_from_cap: bool, cfg: dict,
                      parts: list[dict]) -> tuple[float | None, list[dict], str]:
    """Steps: +/-10%/day cap (unless exempt) -> floor -> ceiling -> noise filter.

    Returns (proposed_or_None, parts, extra_reason_suffix). ``None`` means the
    move was clamped away to nothing (dropped, or below the noise floor).
    """
    max_move = cfg.get("max_move_pct", 0.10)
    if not exempt_from_cap and rules.get("max_move", True):
        lo, hi = formula * (1 - max_move), formula * (1 + max_move)
        if raw < lo or raw > hi:
            clamped = min(max(raw, lo), hi)
            parts.append({"label": f"+/-{int(max_move * 100)}%/day cap",
                          "pct": round((clamped - raw) / formula * 100, 1)})
            raw = clamped

    suffix = ""
    proposed = raw
    if rules.get("rate_floor", True) and proposed < room_type.floor:
        parts.append({"label": f"{room_type.floor:g} floor",
                      "pct": round((room_type.floor - proposed) / formula * 100, 1)})
        proposed = room_type.floor
        suffix = f" - cut capped by your {room_type.floor:g} floor"
        if proposed <= current:
            return None, parts, suffix  # no longer beats the published rate
    if rules.get("rate_ceiling", True) and proposed > room_type.ceiling:
        parts.append({"label": f"{room_type.ceiling:g} ceiling",
                      "pct": round((room_type.ceiling - proposed) / formula * 100, 1)})
        proposed = room_type.ceiling
        suffix = f" - rise capped by your {room_type.ceiling:g} ceiling"

    proposed = round_to(proposed, cfg.get("round_to", 5))
    if abs(proposed - current) < cfg.get("min_move_eur", 3):
        return None, parts, suffix  # not worth proposing
    return proposed, parts, suffix


def draft_cell_proposal(room_type: RoomTypeCfg, night: Night, by_date: dict[str, list[Night]],
                        events: list[Event], comps: list[CompRate], rules: dict,
                        cfg: dict) -> Proposal | None:
    formula = formula_rate(room_type, night.date, cfg)
    current = current_rate(night, formula)
    rows = by_date[night.date]
    occ = night.occupancy_pct
    pace = night.pace_vs_ly_pts
    available = night.capacity - night.otb_rooms

    # 7a - gap night. Exempt from the daily cap; still floor/ceiling clamped.
    if rules.get("gap_fill", True) and available > 0 and is_gap_night(by_date, night.date, cfg):
        raw = formula * cfg.get("gap_cut_pct", 0.80)
        parts = [{"label": "Gap-night fill", "pct": -20}]
        proposed, parts, suffix = _clamp_and_finish(room_type, formula, raw, current, rules,
                                                    True, cfg, parts)
        if proposed is not None:
            prev_occ = max(r.occupancy_pct for r in by_date[
                (date.fromisoformat(night.date) - timedelta(days=1)).isoformat()])
            next_occ = max(r.occupancy_pct for r in by_date[
                (date.fromisoformat(night.date) + timedelta(days=1)).isoformat()])
            reason = (f"1-night hole between two near-full nights - a discounted night "
                     f"beats an empty one. ({prev_occ:.0f}% / {occ:.0f}% / "
                     f"{next_occ:.0f}%){suffix}")
            return _finish_proposal(night, "gap_night", current, proposed, formula, reason, parts)

    # 7b - slow-market deep cut.
    if (pace <= cfg.get("deep_cut_pace_pts", -11) and occ < cfg.get("deep_cut_occ_pct", 55)):
        raw = formula * cfg.get("deep_cut_pct", 0.75)
        parts = [{"label": "Slow-market deep cut", "pct": -25}]
        proposed, parts, suffix = _clamp_and_finish(room_type, formula, raw, current, rules,
                                                    False, cfg, parts)
        if proposed is not None:
            reason = (f"pace {pace:g} pts vs last year with {occ:.0f}% on the books - "
                     f"deep cut to find the market{suffix}")
            return _finish_proposal(night, "rate", current, proposed, formula, reason, parts)
        return None

    # 7c - the ordinary daily move: an additive percentage stack.
    parts: list[dict] = []
    pct_total = 0.0
    reason_bits: list[str] = []
    uplift = uplift_event_for(events, night.date) if rules.get("event_radar", True) else None
    if uplift is not None:
        pct = cfg["move_pct"]["event_congress" if uplift.kind == "congress"
                              else "event_other"]
        pct_total += pct
        parts.append({"label": uplift.name, "pct": round(pct * 100)})
        reason_bits.append(f"{uplift.name} ({pct * 100:+.0f}%)")
    if rules.get("pace_moves", True):
        threshold = cfg.get("pace_up_threshold_pts", 3)
        if pace > threshold and occ > 80:
            pct = cfg["move_pct"]["pace_up"]
            pct_total += pct
            parts.append({"label": "Pickup pace", "pct": round(pct * 100)})
            reason_bits.append(f"pace +{pace:g} pts with {occ:.0f}% already sold "
                               f"({pct * 100:+.0f}%)")
        elif pace < -threshold:
            pct = cfg["move_pct"]["pace_down"]
            pct_total += pct
            parts.append({"label": "Pickup pace", "pct": round(pct * 100)})
            reason_bits.append(f"pace {pace:g} pts vs last year ({pct * 100:+.0f}%)")
    if rules.get("comp_guard", True):
        median = comp_median_multiplier(comps, night.date, room_type.id)
        if median > cfg.get("comp_guard_threshold", 1.10) and pct_total < 0.06:
            pct = cfg["move_pct"]["comp_guard"]
            pct_total += pct
            parts.append({"label": "Comp-set position", "pct": round(pct * 100)})
            reason_bits.append(f"comp set {round((median - 1) * 100)}% above us "
                               f"({pct * 100:+.0f}%)")
    if pct_total == 0:
        return None
    raw = formula * (1 + pct_total)
    proposed, parts, suffix = _clamp_and_finish(room_type, formula, raw, current, rules,
                                                False, cfg, parts)
    if proposed is None:
        return None
    reason = "; ".join(reason_bits) + suffix
    return _finish_proposal(night, "rate", current, proposed, formula, reason, parts)


def _finish_proposal(night: Night, kind: str, current: float, proposed: float, formula: float,
                     reason: str, parts: list[dict]) -> Proposal:
    delta_pct = round((proposed - formula) / formula * 100, 1) if formula else 0.0
    return Proposal(date=night.date, room_type_id=night.room_type_id, kind=kind,
                    current=current, proposed=proposed, formula=formula, delta_pct=delta_pct,
                    reason=reason, parts=parts)


# --------------------------------------------------------------------------
# 7d - length-of-stay offers, date-level
# --------------------------------------------------------------------------
def offer_proposals(nights: list[Night], by_date: dict[str, list[Night]],
                    already_moved_dates: set[str], rules: dict, cfg: dict) -> list[Proposal]:
    if not rules.get("pace_moves", True):
        return []
    candidates = []
    for day, rows in by_date.items():
        if day in already_moved_dates:
            continue
        occ = date_occupancy_pct(rows)
        pace = date_pace_pts(rows)
        if occ < cfg.get("offer_occ_pct", 55) and pace <= cfg.get("offer_pace_pts", -8):
            candidates.append((occ, day, pace))
    candidates.sort()
    out = []
    for occ, day, pace in candidates[: cfg.get("max_offers", 3)]:
        out.append(Proposal(
            date=day, kind="offer", current=None, proposed=None, delta_pct=0.0,
            reason=(f'Open "Stay 3, pay 2" offer - {occ:.0f}% on the books with pace '
                   f"{pace:g} pts behind last year. A length-of-stay deal fills three "
                   f"nights without cutting the headline rate.")))
    return out


# --------------------------------------------------------------------------
# the main entry point - 10 visible steps
# --------------------------------------------------------------------------
def run_repricing(nights: list[Night], comps: list[CompRate], events: list[Event],
                  rules: dict, cfg: dict, today: str | None = None) -> RepricingResult:
    """Pure function: nights + comps + events + rules -> proposals + a log a human can read."""
    today = today or date.today().isoformat()
    room_types = room_types_from_cfg(cfg)
    by_date = _by_date(nights)
    dates = sorted(by_date)
    log: list[str] = []

    total_capacity = sum(n.capacity for n in nights)
    total_otb = sum(n.otb_rooms for n in nights)
    tonight = by_date.get(today, [])
    log.append(
        f"Read the live book - {len(dates)} nights x {len(room_types)} room types "
        f"({len(nights)} price cells). {total_otb} of {total_capacity} rooms on the "
        f"books ({round(100 * total_otb / total_capacity) if total_capacity else 0}% "
        f"of capacity). Tonight: {date_occupancy_pct(tonight)}% occupied.")

    if rules.get("pace_moves", True):
        ahead = sum(1 for d in dates if date_pace_pts(by_date[d]) > 3)
        behind = sum(1 for d in dates if date_pace_pts(by_date[d]) < -3)
        log.append(f"Pickup pace vs last year - {ahead} night(s) pacing more than 3 pts "
                  f"ahead, {behind} pacing behind. Divergent days get repriced; the "
                  f"rest are left alone.")
    else:
        log.append("Pace-based moves are disabled by rule - skipping the pace comparison.")

    if rules.get("comp_guard", True) and comps:
        busiest = max(dates, key=lambda d: date_occupancy_pct(by_date[d])) if dates else today
        log.append(f"Competitor scan - comp-set median {comp_median_multiplier(comps, today)}x "
                  f"tonight, {comp_median_multiplier(comps, busiest)}x on the busiest night.")
    elif rules.get("comp_guard", True):
        log.append("Competitor scan - no comp-rate data ingested; see docs/integrations.md.")
    else:
        log.append("Competitor scan disabled by rule.")

    active_events = [e for e in events if e.category == "event"
                    and any(e.covers(d) for d in dates)]
    if rules.get("event_radar", True):
        log.append(f"Event radar - {len(active_events)} event(s) in the window"
                  + (": " + ", ".join(e.name for e in active_events) if active_events else "."))
    else:
        log.append("Event radar disabled by rule - no event covers this window.")

    gap_nights = [d for d in dates if is_gap_night(by_date, d, cfg)] \
        if rules.get("gap_fill", True) else []
    log.append(f"Gap nights - {len(gap_nights)} found." if rules.get("gap_fill", True)
              else "Gap-night detection disabled by rule.")

    proposals: list[Proposal] = []
    for day in dates:
        mp = mlos_proposal(day, by_date[day], events, rules, cfg)
        if mp is not None:
            proposals.append(mp)
    log.append(f"Stay rules (MLOS) - {sum(1 for p in proposals if p.kind == 'mlos')} "
              f"change(s) proposed.")

    moved_dates: set[str] = set()
    # Offer candidates exclude only a gap-night fill or a deep cut (the spec's own
    # rule) - an ordinary move (e.g. a mild pace-down cut) does not disqualify a
    # date from also getting a length-of-stay offer.
    hard_moved_dates: set[str] = set()
    clamped = 0
    floor_holds = 0
    for day in dates:
        for row in by_date[day]:
            rt = room_types.get(row.room_type_id)
            if rt is None:
                continue
            prop = draft_cell_proposal(rt, row, by_date, events, comps, rules, cfg)
            if prop is not None:
                proposals.append(prop)
                moved_dates.add(day)
                if prop.kind == "gap_night" or any(
                        p["label"] == "Slow-market deep cut" for p in prop.parts):
                    hard_moved_dates.add(day)
                if any("cap" in p["label"] for p in prop.parts):
                    clamped += 1
                if any("floor" in p["label"] for p in prop.parts):
                    floor_holds += 1
    rate_moves = [p for p in proposals if p.kind in ("rate", "gap_night")]
    log.append(f"Draft rate proposals - {len(rate_moves)} across {len(moved_dates)} night(s).")

    offers = offer_proposals(nights, by_date, hard_moved_dates, rules, cfg)
    proposals += offers

    candidates = len(dates) * len(room_types) * CURVE_POINTS
    log.append(f"Searched the price space - tested {candidates} candidate prices "
              f"({len(dates) * len(room_types)} cells x {CURVE_POINTS} price points, "
              f"+/-{int(CURVE_SPAN * 100)}% around formula) against each night's "
              f"demand curve.")

    if clamped or floor_holds:
        log.append(f"Guardrail check - {clamped} proposal(s) clamped by the daily cap, "
                  f"{floor_holds} cut(s) held at the floor.")
    else:
        log.append("Guardrail check - nothing needed clamping this run.")

    uplift = sum(_projected_uplift(p, by_date) for p in proposals if p.kind in ("rate", "gap_night"))
    log.append(f"Decision - {len(rate_moves)} move(s) across {len(moved_dates)} night(s) "
              f"(incl. {sum(1 for p in proposals if p.kind == 'gap_night')} gap-night fill(s), "
              f"{sum(1 for p in proposals if p.kind == 'mlos')} stay-rule change(s), "
              f"{len(offers)} offer(s)) - projected {uplift:+.0f} {cfg.get('currency', 'EUR')} "
              f"on rooms still to sell.")

    summary = {
        "today": today, "nights_scanned": len(dates), "room_types": len(room_types),
        "total_capacity": total_capacity, "total_otb": total_otb,
        "moves": len(rate_moves), "gap_nights": sum(1 for p in proposals if p.kind == "gap_night"),
        "mlos_changes": sum(1 for p in proposals if p.kind == "mlos"), "offers": len(offers),
        "projected_uplift": round(uplift, 2), "clamped": clamped, "floor_holds": floor_holds,
    }
    return RepricingResult(proposals=proposals, thinking_log=log, summary=summary)


def _projected_uplift(p: Proposal, by_date: dict[str, list[Night]]) -> float:
    """Deliberately conservative - see docs/how-it-works.md step 10."""
    if p.current is None or p.proposed is None:
        return 0.0
    rows = [r for r in by_date.get(p.date, []) if r.room_type_id == p.room_type_id]
    remaining = (rows[0].capacity - rows[0].otb_rooms) if rows else 0
    delta = p.proposed - p.current
    if delta >= 0:
        return delta * remaining * 0.5  # assume only half of the up-move sells
    return remaining * (0.12 * p.proposed + 0.4 * delta)


# --------------------------------------------------------------------------
# autopilot classification
# --------------------------------------------------------------------------
def classify_proposals(proposals: list[Proposal], nights: list[Night], comps: list[CompRate],
                       rules: dict, cfg: dict, mode: str, today: str | None = None
                       ) -> list[Proposal]:
    """Sets ``.auto`` and ``.hold_reason`` on every proposal. First gate wins."""
    today = today or date.today().isoformat()
    by_date = _by_date(nights)
    room_types = room_types_from_cfg(cfg)
    near_days = cfg.get("near_manual_days", 3)
    for p in proposals:
        if p.kind == "offer":
            p.auto, p.hold_reason = True, ""  # informational only, nothing to publish
            continue
        offset = (date.fromisoformat(p.date) - date.fromisoformat(today)).days
        if mode == "advise":
            p.hold_reason = "Advise mode - every proposal waits for a human."
            continue
        if rules.get("near_manual", True) and 0 <= offset <= near_days:
            p.hold_reason = (f"Inside the {near_days}-night manual window - arrivals this "
                             f"close are the duty manager's call")
            continue
        if rules.get("hold_threshold", True) and p.kind == "mlos":
            p.hold_reason = "Stay-rule changes are held for review by rule."
            continue
        if (rules.get("hold_threshold", True) and p.kind in ("rate", "gap_night")
                and p.delta_pct is not None and p.delta_pct <= cfg.get("hold_cut_pct", -8)):
            p.hold_reason = (f"Cut of {p.delta_pct:g}% is deeper than the "
                             f"{abs(cfg.get('hold_cut_pct', -8))}% auto-publish threshold")
            continue
        if (rules.get("comp_distance", True) and p.kind in ("rate", "gap_night")
                and p.room_type_id in room_types):
            rt = room_types[p.room_type_id]
            formula = formula_rate(rt, p.date, cfg)
            median = comp_median_multiplier(comps, p.date, p.room_type_id)
            band = cfg.get("comp_distance_pct", 0.15)
            ratio = (p.proposed or formula) / (formula * median) if formula and median else 1.0
            if ratio < 1 - band or ratio > 1 + band:
                p.hold_reason = (f"{round(ratio * 100)}% of the comp-set median - outside "
                                 f"the +/-{int(band * 100)}% guardrail")
                continue
        p.auto = True
    return proposals


# --------------------------------------------------------------------------
# Simple mode - a four-input counter-offer
# --------------------------------------------------------------------------
AGGRESSIVENESS = {
    "very_low": 0.4, "low": 0.7, "standard": 1.0, "high": 1.4, "very_high": 1.8,
}


def _pickup_still_to_come(offset: int) -> float:
    return min(30.0, 2.0 + offset * 1.6)


def run_simple_pricing(nights: list[Night], comps: list[CompRate], cfg: dict,
                       today: str | None = None) -> RepricingResult:
    """The RoomPriceGenie-class counter-offer: base/min/max/target + aggressiveness."""
    today = today or date.today().isoformat()
    simple = cfg["simple"]
    room_types = room_types_from_cfg(cfg)
    ref = room_types[simple_reference_room_type(cfg)]
    coef = AGGRESSIVENESS.get(simple.get("aggressiveness", "standard"), 1.0)
    by_date = _by_date(nights)
    log = [f"Simple mode - base {simple['base_price']:g}, min {simple['min_price']:g}, "
          f"max {simple['max_price']:g}, target occupancy {simple['target_occupancy_pct']}%, "
          f"aggressiveness {simple.get('aggressiveness', 'standard')}."]
    proposals: list[Proposal] = []
    for day, rows in sorted(by_date.items()):
        offset = (date.fromisoformat(day) - date.fromisoformat(today)).days
        if offset < 0:
            continue
        d = date.fromisoformat(day)
        season = cfg["season_multiplier"][d.month - 1] * (
            cfg.get("weekend_multiplier", 1.08) if d.weekday() in (4, 5) else 1.0)
        market = min(1.2, max(0.8, comp_median_multiplier(comps, day, ref.id)))
        occ = date_occupancy_pct(rows)
        expected = max(20.0, simple["target_occupancy_pct"] - _pickup_still_to_come(offset))
        occ_factor = min(1.3, max(0.75, 1 + coef * ((occ - expected) / 100) * 1.2))
        reference = round_to(
            min(simple["max_price"], max(simple["min_price"],
                                          simple["base_price"] * season * market * occ_factor)),
            cfg.get("round_to", 5))
        for row in rows:
            rt = room_types.get(row.room_type_id)
            if rt is None:
                continue
            proposed = reference if rt.id == ref.id else round_to(
                reference * rt.base_rate / ref.base_rate, cfg.get("round_to", 5))
            current = current_rate(row, proposed)
            if abs(proposed - current) < cfg.get("min_move_eur", 3):
                continue
            reason = (f"base {simple['base_price']:g} x season/weekend {season:.2f} -> "
                     f"market {market:.2f} -> occupancy {occ:.0f}% vs expected "
                     f"{expected:.0f}% (factor {occ_factor:.2f}) -> {reference:g}, "
                     f"clamped to [{simple['min_price']:g}, {simple['max_price']:g}]")
            proposals.append(Proposal(date=day, room_type_id=rt.id, kind="rate",
                                      current=current, proposed=proposed,
                                      delta_pct=round((proposed - current) / current * 100, 1)
                                      if current else 0.0, reason=reason))
    log.append(f"Decision - {len(proposals)} move(s) proposed across "
              f"{len({p.date for p in proposals})} night(s).")
    summary = {"today": today, "engine": "simple", "moves": len(proposals)}
    return RepricingResult(proposals=proposals, thinking_log=log, summary=summary)


# --------------------------------------------------------------------------
# the "why" drawer - a calibrated price-response curve (see docs/how-it-works.md #5)
# --------------------------------------------------------------------------
def price_response_curve(formula: float, proposed: float, current: float) -> dict:
    """31 candidate prices across +/-30% of formula, calibrated so the curve's own
    peak lands on ``proposed`` - the chart and the proposal can never disagree.

    This mirrors the source engine's own honestly-documented mechanism: the
    guardrail stack picks the price first, and the curve is fitted to explain
    it afterwards. It is a legitimate way to show contribution and elasticity
    together; it is not a search that chose the number.
    """
    if formula <= 0:
        return {"points": [], "x0": 1.0}
    ratio_target = proposed / formula
    best_x0, best_err = 1.0, float("inf")
    for i in range(110, 351):  # 0.550 .. 1.750 step 0.005
        x0 = i / 200
        best_ratio, best_val = 1.0, -1.0
        for j in range(CURVE_POINTS):
            ratio = (1 - CURVE_SPAN) + j * (2 * CURVE_SPAN) / (CURVE_POINTS - 1)
            p_book = 1 / (1 + math.exp(CURVE_K * (ratio - x0)))  # decreasing in price
            value = ratio * p_book
            if value > best_val:
                best_val, best_ratio = value, ratio
        err = abs(best_ratio - ratio_target)
        if err < best_err:
            best_err, best_x0 = err, x0
    points = []
    for j in range(CURVE_POINTS):
        ratio = (1 - CURVE_SPAN) + j * (2 * CURVE_SPAN) / (CURVE_POINTS - 1)
        price = round_to(formula * ratio, 5)
        p_book = round(1 / (1 + math.exp(CURVE_K * (ratio - best_x0))), 3)
        points.append({"price": price, "p_book": p_book,
                       "expected_revenue": round(price * p_book, 2)})
    return {"points": points, "optimal_price": round_to(proposed, 5),
           "current_price": round_to(current, 5), "x0": round(best_x0, 3)}
