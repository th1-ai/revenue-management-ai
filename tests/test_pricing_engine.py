"""Tests for tools/pricing_engine.py - the Quant's pure decision engine.

No adapters, no store, no network: every test builds its own tiny
`Night`/`CompRate`/`Event` rows and checks the engine's arithmetic and
guardrails directly. `tests/test_run_loop.py` covers the end-to-end path on
the bundled fixtures.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.pricing_engine import (CompRate, Event, Night, RoomTypeCfg, SimpleEngineConfigError,
                                  classify_proposals, comp_median_multiplier, current_rate,
                                  formula_rate, is_gap_night, mlos_proposal, offer_proposals,
                                  price_response_curve, round_to, room_types_from_cfg,
                                  run_repricing, run_simple_pricing, simple_reference_room_type)

CFG = {
    "room_types": {
        "classic": {"name": "Classic Room", "base_rate": 140, "floor": 95, "ceiling": 220},
        "deluxe": {"name": "Deluxe Room", "base_rate": 190, "floor": 130, "ceiling": 290},
    },
    "season_multiplier": [0.85, 0.85, 0.90, 1.00, 1.10, 1.25, 1.35, 1.35, 1.15, 1.00, 0.90, 0.95],
    "weekend_multiplier": 1.08,
    "round_to": 5,
    "move_pct": {"event_congress": 0.08, "event_other": 0.04, "pace_up": 0.05,
                "pace_down": -0.05, "comp_guard": 0.04},
    "pace_up_threshold_pts": 3,
    "comp_guard_threshold": 1.10,
    "max_move_pct": 0.10,
    "min_move_eur": 3,
    "deep_cut_pace_pts": -11,
    "deep_cut_occ_pct": 55,
    "deep_cut_pct": 0.75,
    "gap_neighbour_occ_pct": 85,
    "gap_drop_pts": 25,
    "gap_cut_pct": 0.80,
    "mlos_event_occ_pct": 80,
    "mlos_weekend_occ_pct": 90,
    "mlos_release_occ_pct": 60,
    "offer_occ_pct": 55,
    "offer_pace_pts": -8,
    "max_offers": 3,
    "near_manual_days": 3,
    "hold_cut_pct": -8,
    "comp_distance_pct": 0.15,
}
RULES_ALL_ON = {k: True for k in (
    "event_radar", "comp_guard", "pace_moves", "rate_floor", "rate_ceiling", "max_move",
    "mlos_guard", "gap_fill", "near_manual", "hold_threshold", "comp_distance")}
CLASSIC = RoomTypeCfg(id="classic", name="Classic Room", base_rate=140, floor=95, ceiling=220)


def test_formula_rate_matches_the_house_formula():
    # 2026-09-08 is a Tuesday: no weekend multiplier. September = index 8 = 1.15.
    rate = formula_rate(CLASSIC, "2026-09-08", CFG)
    assert rate == round_to(140 * 1.15, 5) == 160


def test_formula_rate_applies_weekend_multiplier_on_friday_and_saturday():
    friday = formula_rate(CLASSIC, "2026-09-04", CFG)
    tuesday = formula_rate(CLASSIC, "2026-09-08", CFG)
    assert friday > tuesday  # same month, weekend lifts it


def test_current_rate_prefers_the_override():
    night = Night(date="2026-09-08", room_type_id="classic", capacity=20, otb_rooms=10,
                 rate_override=175)
    assert current_rate(night, formula_rate(CLASSIC, "2026-09-08", CFG)) == 175


def test_current_rate_falls_back_to_formula_with_no_override():
    night = Night(date="2026-09-08", room_type_id="classic", capacity=20, otb_rooms=10)
    formula = formula_rate(CLASSIC, "2026-09-08", CFG)
    assert current_rate(night, formula) == formula


def test_comp_median_multiplier_is_neutral_with_no_data():
    assert comp_median_multiplier([], "2026-09-08") == 1.0


def test_comp_median_multiplier_reads_the_right_night():
    comps = [CompRate(competitor="A", date="2026-09-08", rate_multiplier=1.2),
             CompRate(competitor="B", date="2026-09-08", rate_multiplier=1.0),
             CompRate(competitor="A", date="2026-09-09", rate_multiplier=0.5)]
    assert comp_median_multiplier(comps, "2026-09-08") == 1.1


def test_room_types_from_cfg_reads_the_id_keyed_mapping():
    room_types = room_types_from_cfg(CFG)
    assert set(room_types) == {"classic", "deluxe"}
    assert room_types["classic"].floor == 95


def _rows(date_occ: dict[str, float], capacity: int = 20) -> dict[str, list[Night]]:
    return {d: [Night(date=d, room_type_id="classic", capacity=capacity,
                      otb_rooms=round(occ / 100 * capacity))]
           for d, occ in date_occ.items()}


def test_gap_night_needs_both_neighbours_full():
    by_date = _rows({"2026-09-03": 91, "2026-09-04": 58, "2026-09-05": 88})
    assert is_gap_night(by_date, "2026-09-04", CFG) is True


def test_gap_night_false_when_a_neighbour_is_soft():
    by_date = _rows({"2026-09-03": 91, "2026-09-04": 58, "2026-09-05": 60})
    assert is_gap_night(by_date, "2026-09-04", CFG) is False


def test_mlos_sets_two_nights_on_an_uplift_event():
    rows = [Night(date="2026-09-08", room_type_id="classic", capacity=20, otb_rooms=17)]
    events = [Event(name="Tech Summit", kind="congress", category="event",
                    start_date="2026-09-08", end_date="2026-09-09")]
    prop = mlos_proposal("2026-09-08", rows, events, RULES_ALL_ON, CFG)
    assert prop is not None and prop.proposed_mlos == 2 and prop.current_mlos == 1


def test_mlos_releases_once_demand_fades():
    rows = [Night(date="2026-09-14", room_type_id="classic", capacity=20, otb_rooms=8,
                  mlos_override=2)]
    prop = mlos_proposal("2026-09-14", rows, [], RULES_ALL_ON, CFG)
    assert prop is not None and prop.proposed_mlos == 1 and prop.current_mlos == 2


def test_mlos_guard_off_proposes_nothing():
    rows = [Night(date="2026-09-08", room_type_id="classic", capacity=20, otb_rooms=20)]
    rules = {**RULES_ALL_ON, "mlos_guard": False}
    assert mlos_proposal("2026-09-08", rows, [], rules, CFG) is None


def test_rate_floor_clamps_a_deep_cut_and_drops_if_it_no_longer_beats_current():
    # A high floor (150) sits above the cap-limited deep cut (formula x 0.9 = 144),
    # so the proposal is lifted to the floor - and since the floor (150) does not
    # beat the guest's current rate (155), the whole proposal is dropped.
    cfg = {**CFG, "room_types": {"classic": {"name": "Classic Room", "base_rate": 140,
                                             "floor": 150, "ceiling": 400}}}
    nights = [Night(date="2026-09-13", room_type_id="classic", capacity=20, otb_rooms=8,
                    pace_vs_ly_pts=-14, rate_override=155)]
    result = run_repricing(nights, [], [], RULES_ALL_ON, cfg, today="2026-09-01")
    rate_cell = next((p for p in result.proposals
                      if p.date == "2026-09-13" and p.kind in ("rate", "gap_night")), None)
    assert rate_cell is None  # dropped: the floor-clamped cut (150) does not beat 155


def test_rate_ceiling_clamps_a_rise():
    cfg = {**CFG, "room_types": {"classic": {"name": "Classic Room", "base_rate": 140,
                                             "floor": 95, "ceiling": 165}}}
    nights = [Night(date="2026-09-08", room_type_id="classic", capacity=20, otb_rooms=19,
                    pace_vs_ly_pts=10)]
    events = [Event(name="Tech Summit", kind="congress", category="event",
                    start_date="2026-09-08", end_date="2026-09-08")]
    result = run_repricing(nights, [], events, RULES_ALL_ON, cfg, today="2026-09-01")
    prop = next(p for p in result.proposals if p.kind == "rate")
    assert prop.proposed <= 165


def test_max_move_cap_clamps_the_ordinary_move():
    nights = [Night(date="2026-09-08", room_type_id="classic", capacity=20, otb_rooms=19,
                    pace_vs_ly_pts=10)]
    events = [Event(name="Tech Summit", kind="congress", category="event",
                    start_date="2026-09-08", end_date="2026-09-08")]
    result = run_repricing(nights, [], events, RULES_ALL_ON, CFG, today="2026-09-01")
    prop = next(p for p in result.proposals if p.kind == "rate")
    formula = formula_rate(CLASSIC, "2026-09-08", CFG)
    assert prop.proposed <= formula * 1.10 + 1e-9
    assert any("cap" in part["label"] for part in prop.parts)


def test_deep_cut_fires_on_soft_slow_pace():
    nights = [Night(date="2026-09-14", room_type_id="classic", capacity=20, otb_rooms=8,
                    pace_vs_ly_pts=-14)]
    result = run_repricing(nights, [], [], RULES_ALL_ON, CFG, today="2026-09-01")
    prop = next(p for p in result.proposals if p.date == "2026-09-14")
    formula = formula_rate(CLASSIC, "2026-09-14", CFG)
    assert prop.proposed < formula


def test_offer_excludes_gap_and_deep_cut_dates_only():
    nights = [
        Night(date="2026-09-10", room_type_id="classic", capacity=20, otb_rooms=10,
             pace_vs_ly_pts=-14),  # deep cut - excluded from offers
        Night(date="2026-09-11", room_type_id="classic", capacity=20, otb_rooms=9,
             pace_vs_ly_pts=-9),  # ordinary pace-down move only - still offer-eligible
    ]
    by_date = {"2026-09-10": [nights[0]], "2026-09-11": [nights[1]]}
    offers = offer_proposals(nights, by_date, {"2026-09-10"}, RULES_ALL_ON, CFG)
    assert [o.date for o in offers] == ["2026-09-11"]


def test_classify_holds_the_near_manual_window():
    from tools.pricing_engine import Proposal
    props = [Proposal(date="2026-09-02", room_type_id="classic", kind="rate",
                      current=140, proposed=150, delta_pct=5.0)]
    nights = [Night(date="2026-09-02", room_type_id="classic", capacity=20, otb_rooms=10)]
    classify_proposals(props, nights, [], RULES_ALL_ON, CFG, "guarded", today="2026-09-01")
    assert props[0].auto is False and "manual window" in props[0].hold_reason


def test_classify_always_holds_mlos_even_in_full_mode():
    from tools.pricing_engine import Proposal
    props = [Proposal(date="2026-09-10", kind="mlos", current_mlos=1, proposed_mlos=2)]
    nights = [Night(date="2026-09-10", room_type_id="classic", capacity=20, otb_rooms=18)]
    classify_proposals(props, nights, [], RULES_ALL_ON, CFG, "full", today="2026-09-01")
    assert props[0].auto is False and "Stay-rule" in props[0].hold_reason


def test_classify_holds_a_cut_deeper_than_the_threshold():
    from tools.pricing_engine import Proposal
    props = [Proposal(date="2026-09-10", room_type_id="classic", kind="rate",
                      current=160, proposed=140, delta_pct=-12.5)]
    nights = [Night(date="2026-09-10", room_type_id="classic", capacity=20, otb_rooms=10)]
    classify_proposals(props, nights, [], RULES_ALL_ON, CFG, "guarded", today="2026-09-01")
    assert props[0].auto is False and "auto-publish threshold" in props[0].hold_reason


def test_classify_auto_eligible_when_no_gate_fires():
    from tools.pricing_engine import Proposal
    props = [Proposal(date="2026-09-10", room_type_id="classic", kind="rate",
                      current=160, proposed=163, delta_pct=1.9)]
    nights = [Night(date="2026-09-10", room_type_id="classic", capacity=20, otb_rooms=10)]
    classify_proposals(props, nights, [], RULES_ALL_ON, CFG, "guarded", today="2026-09-01")
    assert props[0].auto is True and props[0].hold_reason == ""


def test_run_simple_pricing_stays_within_min_and_max():
    cfg = {**CFG, "simple": {"reference_room_type": "classic", "base_price": 130,
                             "min_price": 95, "max_price": 300,
                             "target_occupancy_pct": 80, "aggressiveness": "standard"}}
    nights = [Night(date="2026-09-08", room_type_id="classic", capacity=20, otb_rooms=12)]
    result = run_simple_pricing(nights, [], cfg, today="2026-09-01")
    for p in result.proposals:
        assert 95 <= p.proposed <= 300


def test_simple_pricing_gives_a_readable_error_when_room_types_are_renamed():
    """Regression for the BLOCKER in SIMULATION.md: a hotelier follows
    workflows/00-setup.md step 3, replaces `room_types` with their own ids,
    but forgets `simple.reference_room_type` still says the shipped
    'classic'. This must raise one readable line naming the bad value and
    the valid options - never a bare KeyError."""
    cfg = {**CFG, "room_types": {"standard": {"name": "Standard", "base_rate": 120,
                                              "floor": 90, "ceiling": 200},
                                 "seeblick": {"name": "Lake View", "base_rate": 165,
                                             "floor": 120, "ceiling": 260},
                                 "suite": {"name": "Suite", "base_rate": 240,
                                          "floor": 180, "ceiling": 400}},
          "simple": {"reference_room_type": "classic", "base_price": 130,
                    "min_price": 95, "max_price": 300,
                    "target_occupancy_pct": 80, "aggressiveness": "standard"}}
    nights = [Night(date="2026-09-08", room_type_id="standard", capacity=20, otb_rooms=12)]
    with pytest.raises(SimpleEngineConfigError) as exc_info:
        run_simple_pricing(nights, [], cfg, today="2026-09-01")
    message = str(exc_info.value)
    assert "classic" in message  # names the bad value
    assert "standard" in message and "seeblick" in message and "suite" in message  # the options
    # the doctor check (tools/doctor.py:check_simple_engine) shares this exact function
    with pytest.raises(SimpleEngineConfigError):
        simple_reference_room_type(cfg)


def test_simple_pricing_works_once_reference_room_type_is_updated():
    cfg = {**CFG, "room_types": {"standard": {"name": "Standard", "base_rate": 120,
                                              "floor": 90, "ceiling": 200}},
          "simple": {"reference_room_type": "standard", "base_price": 130,
                    "min_price": 95, "max_price": 300,
                    "target_occupancy_pct": 80, "aggressiveness": "standard"}}
    nights = [Night(date="2026-09-08", room_type_id="standard", capacity=20, otb_rooms=12)]
    assert simple_reference_room_type(cfg) == "standard"
    result = run_simple_pricing(nights, [], cfg, today="2026-09-01")
    assert result.summary["engine"] == "simple"


def test_price_response_curve_peak_lands_on_the_proposed_price():
    curve = price_response_curve(160, 176, 160)
    best = max(curve["points"], key=lambda pt: pt["expected_revenue"])
    assert best["price"] == curve["optimal_price"]


def test_price_response_curve_probability_falls_as_price_rises():
    curve = price_response_curve(160, 176, 160)
    probs = [pt["p_book"] for pt in curve["points"]]
    assert probs == sorted(probs, reverse=True)  # monotonically decreasing
