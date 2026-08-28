"""tools/forecast_engine.py - Demand Forecasting AI ("The Oracle"). Pure functions.

A 21-night occupancy outlook plus a rate-shopping panel. Reads the same
`Night` / `CompRate` / `Event` tables as `tools/pricing_engine.py` but never
writes anything and never sets a price - see docs/how-it-works.md "Design
decisions" #10 for why this is deliberately independent of the repricing
engine rather than quietly feeding it.

Off by default (`config/agent.yaml: subagents.demand_forecasting.enabled`).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from tools.pricing_engine import (CompRate, Event, Night, comp_median_multiplier,
                                  low_demand_event_for, round_to, uplift_event_for)

OCC_CEILING = 98
PICKUP_BASE = 2.0
PICKUP_PER_DAY = 1.6
PICKUP_CAP = 30.0
SHOP_RUN_NIGHTS = 2
SHOP_DEVIATION_PCT = 10
MAX_CALLOUTS = 3


@dataclass
class OccupancyProjection:
    date: str
    otb_pct: float
    base_pickup: float
    event_delta: float
    event_name: str
    weather_delta: float
    weather_name: str
    pickup: float
    projected: float
    comp_note: str | None = None


@dataclass
class ForecastResult:
    projections: list[OccupancyProjection]
    callouts: list[str]
    thinking_log: list[str]
    summary: dict[str, Any]


def _by_date(nights: list[Night]) -> dict[str, list[Night]]:
    out: dict[str, list[Night]] = {}
    for n in nights:
        out.setdefault(n.date, []).append(n)
    return out


def base_pickup(offset: int) -> float:
    """A night 10 days out still has ~18 pts of pickup left; tonight has ~2."""
    return min(PICKUP_CAP, PICKUP_BASE + offset * PICKUP_PER_DAY)


def project_occupancy(nights: list[Night], comps: list[CompRate], events: list[Event],
                      rules: dict, today: str, horizon_nights: int,
                      ref_room_type: str | None) -> list[OccupancyProjection]:
    by_date = _by_date(nights)
    out = []
    for i in range(horizon_nights):
        day = (date.fromisoformat(today) + timedelta(days=i)).isoformat()
        rows = by_date.get(day, [])
        cap = sum(r.capacity for r in rows)
        otb = sum(r.otb_rooms for r in rows)
        otb_pct = round(100 * otb / cap, 1) if cap else 0.0

        event_delta, event_name = 0.0, ""
        low = low_demand_event_for(events, day)
        if low is not None:
            event_delta -= 5
            event_name = low.name
        if rules.get("event_radar", True):
            uplift = uplift_event_for(events, day)
            if uplift is not None:
                event_delta += 12 if uplift.kind == "congress" else 6
                event_name = uplift.name

        weather_delta, weather_name = 0.0, ""
        if rules.get("weather_signal", True):
            weather = next((e for e in events if e.category == "weather" and e.covers(day)),
                           None)
            if weather is not None:
                weather_delta = -6 if weather.kind == "rain" else 3
                weather_name = weather.name

        pickup = max(0.0, base_pickup(i) + event_delta + weather_delta)
        projected = min(OCC_CEILING, round(otb_pct + pickup))

        comp_note = None
        median = comp_median_multiplier(comps, day, ref_room_type)
        pct = round((median - 1) * 100)
        if comps and abs(pct) >= 5:
            comp_note = (f"competitive set is {abs(pct)}% above our rate - headroom to "
                        f"move up" if pct > 0 else
                        f"competitive set is {abs(pct)}% below our rate - pickup has to "
                        f"be won on value")

        out.append(OccupancyProjection(
            date=day, otb_pct=otb_pct, base_pickup=round(base_pickup(i), 1),
            event_delta=event_delta, event_name=event_name, weather_delta=weather_delta,
            weather_name=weather_name, pickup=round(pickup, 1), projected=projected,
            comp_note=comp_note))
    return out


def shop_rates(dates: list[str], comps: list[CompRate], ref_room_type: str | None
              ) -> tuple[list[dict], list[str]]:
    """Per-night comp rows, plus callouts: >=2 consecutive nights, >10% off, merged."""
    rows = []
    for day in dates:
        our_multiplier = 1.0
        median = comp_median_multiplier(comps, day, ref_room_type)
        rivals = {c.competitor: c.rate_multiplier for c in comps
                 if c.date == day and c.room_type_id in (None, ref_room_type)}
        rows.append({"date": day, "median_pct": round((median - 1) * 100), "rivals": rivals})

    # find runs, per rival, of >= SHOP_RUN_NIGHTS consecutive deviating nights
    runs: list[dict] = []
    for comp in {c.competitor for c in comps}:
        by_day = {c.date: c for c in comps if c.competitor == comp}
        current: list[str] = []
        for day in dates + [None]:
            dev = None
            if day is not None:
                row = by_day.get(day)
                dev = round((row.rate_multiplier - 1) * 100) if row else None
            if dev is not None and abs(dev) >= SHOP_DEVIATION_PCT:
                current.append(day)
            else:
                if len(current) >= SHOP_RUN_NIGHTS:
                    first_dev = round((by_day[current[0]].rate_multiplier - 1) * 100)
                    runs.append({
                        "competitor": comp, "nights": list(current),
                        "kind": "undercut" if first_dev < 0 else "premium",
                        "pct_range": sorted({abs(round((by_day[d].rate_multiplier - 1) * 100))
                                            for d in current}),
                        "note": next((by_day[d].note for d in current if by_day[d].note), ""),
                    })
                current = []
    runs.sort(key=lambda r: max(r["pct_range"]) * len(r["nights"]), reverse=True)
    callouts = []
    for run in runs[:MAX_CALLOUTS]:
        lo, hi = min(run["pct_range"]), max(run["pct_range"])
        span = f"{lo}%" if lo == hi else f"{lo}-{hi}%"
        when = f"{run['nights'][0]} to {run['nights'][-1]}" if len(run["nights"]) > 1 \
            else run["nights"][0]
        line = f"{run['competitor']} {span} {'under' if run['kind'] == 'undercut' else 'above'} us {when}."
        if run["note"]:
            line += f' Their note reads "{run["note"]}".'
        line += (" Hold - a discount is a promotion, not the market moving."
                if run["kind"] == "undercut" else " Room to move.")
        callouts.append(line)
    if not callouts:
        callouts.append("Nobody sits more than 10% off our rate for two nights running - "
                        "the set is priced where we are.")
    return rows, callouts


def run_forecast(nights: list[Night], comps: list[CompRate], events: list[Event],
                 rules: dict, cfg: dict, today: str | None = None) -> ForecastResult:
    today = today or date.today().isoformat()
    horizon = cfg.get("horizon_nights", 21)
    ref = cfg.get("simple", {}).get("reference_room_type") or (
        next(iter(cfg.get("room_types") or {}), None))
    dates = [(date.fromisoformat(today) + timedelta(days=i)).isoformat() for i in range(horizon)]

    log = ["Run demand forecast."]
    projections = project_occupancy(nights, comps, events, rules, today, horizon, ref)
    total_cap = sum(n.capacity for n in nights) or 1
    total_otb = sum(n.otb_rooms for n in nights)
    ahead = sum(1 for p in projections if p.otb_pct > 0 and p.pickup > 3)
    log.append(f"Scanned pickup pace across {horizon} nights - "
              f"{round(100 * total_otb / total_cap)}% of capacity on the books, "
              f"{round(base_pickup(0), 1)} pts of pickup for tonight, up to "
              f"{PICKUP_CAP:g} at the far end of the window.")

    shop_rows, callouts = shop_rates(dates, comps, ref)
    log.append("Rate shopping - " + (callouts[0] if callouts else "no comp data ingested."))

    active_events = [e for e in events if e.category == "event" and any(e.covers(d) for d in dates)]
    if rules.get("event_radar", True) and active_events:
        log.append("Event radar - " + "; ".join(
            f"{e.name} ({'+12' if e.kind == 'congress' else '+6' if e.kind == 'regatta' else '-5'} pts)"
            for e in active_events))
    elif rules.get("event_radar", True):
        log.append("Event radar - nothing in the window.")
    else:
        log.append("Event radar disabled by rule - the outlook is read from pace alone.")

    weather_events = [e for e in events if e.category == "weather" and any(e.covers(d) for d in dates)]
    if rules.get("weather_signal", True) and weather_events:
        log.append("Weather front - " + "; ".join(
            f"{e.name} ({'-6' if e.kind == 'rain' else '+3'} pts)" for e in weather_events))
    elif rules.get("weather_signal", True):
        log.append("Weather front - nothing on the forecast.")
    else:
        log.append("Weather signal disabled by rule.")

    avg_otb = round(sum(p.otb_pct for p in projections) / len(projections), 1) if projections else 0
    avg_projected = round(sum(p.projected for p in projections) / len(projections), 1) \
        if projections else 0
    peak = max(projections, key=lambda p: p.projected) if projections else None
    softest = min(projections, key=lambda p: p.projected) if projections else None
    headline = (f"The next {horizon} nights project to finish at {avg_projected}% against "
              f"{avg_otb}% on the books" +
              (f". Strongest: {peak.date} at {peak.projected}%. Softest: {softest.date} "
               f"at {softest.projected}%." if peak and softest else "."))
    log.append("Assemble the outlook - " + headline)

    summary = {
        "today": today, "horizon_nights": horizon, "avg_otb_pct": avg_otb,
        "avg_projected_pct": avg_projected,
        "peak": {"date": peak.date, "pct": peak.projected} if peak else None,
        "softest": {"date": softest.date, "pct": softest.projected} if softest else None,
        "callouts": callouts, "headline": headline,
    }
    return ForecastResult(projections=projections, callouts=callouts, thinking_log=log,
                          summary=summary)
