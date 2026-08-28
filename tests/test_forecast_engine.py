"""Tests for tools/forecast_engine.py - Demand Forecasting AI ("The Oracle").

Independent of the repricing engine on purpose - see docs/how-it-works.md
"Design decisions" #10.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.forecast_engine import (OCC_CEILING, base_pickup, project_occupancy, run_forecast,
                                   shop_rates)
from tools.pricing_engine import CompRate, Event, Night

RULES_ALL_ON = {"event_radar": True, "weather_signal": True}
CFG = {
    "horizon_nights": 5,
    "room_types": {"classic": {"name": "Classic Room", "base_rate": 140, "floor": 95,
                               "ceiling": 220}},
    "season_multiplier": [0.85, 0.85, 0.90, 1.00, 1.10, 1.25, 1.35, 1.35, 1.15, 1.00, 0.90, 0.95],
    "weekend_multiplier": 1.08, "round_to": 5,
    "simple": {"reference_room_type": "classic"},
}


def _nights(otb_by_date: dict[str, int], capacity: int = 20) -> list[Night]:
    return [Night(date=d, room_type_id="classic", capacity=capacity, otb_rooms=otb)
           for d, otb in otb_by_date.items()]


def test_base_pickup_is_capped_and_grows_with_lead_time():
    assert base_pickup(0) == 2.0
    assert base_pickup(10) == 18.0
    assert base_pickup(100) == 30.0  # PICKUP_CAP


def test_projection_never_exceeds_the_occupancy_ceiling():
    nights = _nights({"2026-09-08": 20})  # 100% OTB already
    events = [Event(name="Tech Summit", kind="congress", category="event",
                    start_date="2026-09-08", end_date="2026-09-08")]
    projections = project_occupancy(nights, [], events, RULES_ALL_ON, "2026-09-08", 1, "classic")
    assert projections[0].projected <= OCC_CEILING


def test_weather_signal_toggle_changes_the_projection():
    nights = _nights({"2026-09-06": 8})
    events = [Event(name="Atlantic squall", kind="rain", category="weather",
                    start_date="2026-09-06", end_date="2026-09-06")]
    on = project_occupancy(nights, [], events, {"event_radar": True, "weather_signal": True},
                           "2026-09-06", 1, "classic")[0]
    off = project_occupancy(nights, [], events, {"event_radar": True, "weather_signal": False},
                            "2026-09-06", 1, "classic")[0]
    assert on.weather_delta == -6 and off.weather_delta == 0  # rain drags 6 pts, gated by rule
    assert on.projected <= off.projected  # never a higher projection with the drag applied


def test_low_demand_drag_survives_the_event_radar_toggle():
    nights = _nights({"2026-09-14": 8})
    events = [Event(name="Quiet midweek window", kind="low_demand", category="event",
                    start_date="2026-09-14", end_date="2026-09-14")]
    on = project_occupancy(nights, [], events, {"event_radar": True, "weather_signal": True},
                           "2026-09-14", 1, "classic")[0]
    off = project_occupancy(nights, [], events, {"event_radar": False, "weather_signal": True},
                            "2026-09-14", 1, "classic")[0]
    assert on.event_delta == -5 and off.event_delta == -5  # the drag is not rule-gated


def test_shop_rates_needs_two_consecutive_deviating_nights():
    comps = [CompRate(competitor="Casa Alameda", date="2026-09-20", rate_multiplier=0.75)]
    _, callouts = shop_rates(["2026-09-20"], comps, "classic")
    assert "Nobody sits more than 10%" in callouts[0]


def test_shop_rates_merges_a_two_night_run_into_one_callout():
    comps = [CompRate(competitor="Casa Alameda", date="2026-09-20", rate_multiplier=0.75,
                      note="Flash sale"),
            CompRate(competitor="Casa Alameda", date="2026-09-21", rate_multiplier=0.76)]
    _, callouts = shop_rates(["2026-09-20", "2026-09-21"], comps, "classic")
    assert len(callouts) == 1
    assert "Casa Alameda" in callouts[0] and "Flash sale" in callouts[0]


def test_run_forecast_reads_rate_shopping_and_events_into_one_headline():
    nights = _nights({f"2026-09-0{d}": 10 for d in range(1, 6)})
    events = [Event(name="Tech Summit", kind="congress", category="event",
                    start_date="2026-09-03", end_date="2026-09-03")]
    result = run_forecast(nights, [], events, RULES_ALL_ON, CFG, today="2026-09-01")
    assert result.summary["horizon_nights"] == 5
    assert "project to finish" in result.summary["headline"]
    assert len(result.projections) == 5
