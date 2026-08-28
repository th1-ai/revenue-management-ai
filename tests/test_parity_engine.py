"""Tests for tools/parity_engine.py - OTA Content & Parity AI ("The Cartographer").

No LLM anywhere on this path - see docs/how-it-works.md "Design decisions" #9.
These tests import only stdlib + tools.parity_engine to prove it, the same way
the module itself never imports core.llm.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.parity_engine import (SCORE_FLOOR, ContentFinding, compute_parity_breaks,
                                 content_score, draft_content_fix, draft_parity_fix,
                                 run_channel_sweep)
from tools.pricing_engine import Night, RoomTypeCfg

CFG = {
    "season_multiplier": [0.85, 0.85, 0.90, 1.00, 1.10, 1.25, 1.35, 1.35, 1.15, 1.00, 0.90, 0.95],
    "weekend_multiplier": 1.08, "round_to": 5,
}
ROOM_TYPES = {"classic": RoomTypeCfg(id="classic", name="Classic Room", base_rate=140,
                                    floor=95, ceiling=220)}


def test_no_llm_import_anywhere_in_this_module():
    import tools.parity_engine as mod
    lines = Path(mod.__file__).read_text(encoding="utf-8").splitlines()
    imports = [ln for ln in lines if ln.startswith("import ") or ln.startswith("from ")]
    assert not any("core.llm" in ln or "core.review" in ln for ln in imports)


def test_parity_break_detected_beyond_tolerance():
    nights = [Night(date="2026-09-08", room_type_id="classic", capacity=20, otb_rooms=17)]
    ota_rates = [{"channel": "Booking.com", "date": "2026-09-08", "room_type_id": "classic",
                 "observed_rate": 138}]
    breaks = compute_parity_breaks(nights, ROOM_TYPES, ota_rates, CFG, tolerance_pct=0.01)
    assert len(breaks) == 1 and breaks[0].channel == "Booking.com"
    assert breaks[0].pct_under > 1


def test_parity_within_tolerance_is_not_a_break():
    nights = [Night(date="2026-09-08", room_type_id="classic", capacity=20, otb_rooms=17)]
    direct = nights[0].capacity  # unused, just documenting the fixture
    ota_rates = [{"channel": "Google Hotel Ads", "date": "2026-09-08", "room_type_id": "classic",
                 "observed_rate": 160}]  # matches formula_rate exactly
    breaks = compute_parity_breaks(nights, ROOM_TYPES, ota_rates, CFG, tolerance_pct=0.01)
    assert breaks == []


def test_draft_parity_fix_quotes_the_real_numbers():
    nights = [Night(date="2026-09-08", room_type_id="classic", capacity=20, otb_rooms=17)]
    breaks = compute_parity_breaks(nights, ROOM_TYPES,
                                   [{"channel": "Booking.com", "date": "2026-09-08",
                                     "room_type_id": "classic", "observed_rate": 138}],
                                   CFG, tolerance_pct=0.01)
    draft = draft_parity_fix(breaks[0])
    assert "Booking.com" in draft and "138" in draft and "160" in draft


def test_content_score_floor_is_forty():
    findings = [ContentFinding(channel="Expedia", kind="photos", severity="high", detail="x"),
               ContentFinding(channel="Expedia", kind="amenities", severity="high", detail="y"),
               ContentFinding(channel="Expedia", kind="description", severity="high", detail="z")]
    scores = content_score(findings, ["Expedia"])
    assert scores[0].score == SCORE_FLOOR  # 3 x 22 = 66 deducted, would go below 40


def test_content_score_healthy_with_no_findings():
    scores = content_score([], ["Airbnb"])
    assert scores[0].score == 100 and scores[0].band == "healthy"


def test_applied_finding_no_longer_costs_points():
    findings = [ContentFinding(channel="Expedia", kind="photos", severity="high", detail="x",
                               status="applied")]
    scores = content_score(findings, ["Expedia"])
    assert scores[0].score == 100  # applied findings are not "open"


def test_draft_content_fix_quotes_the_finding_detail():
    finding = ContentFinding(channel="Expedia", kind="photos", severity="high",
                             detail="4 of 16 photos missing from the master set")
    draft = draft_content_fix(finding)
    assert "4 of 16 photos missing" in draft and "Expedia" in draft


def test_channel_sweep_content_sync_off_skips_scoring():
    findings = [ContentFinding(channel="Expedia", kind="photos", severity="high", detail="x")]
    result = run_channel_sweep([], ROOM_TYPES, [], findings, ["Expedia"],
                               {"content_sync": False}, {**CFG, "parity_tolerance_pct": 0.01})
    assert "Content diff skipped" in result.thinking_log[-1] or \
        any("skipped" in line for line in result.thinking_log)
    assert result.scores[0].score == 100  # rates-only sweep does not score content
