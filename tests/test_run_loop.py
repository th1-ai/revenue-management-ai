"""Integration tests: the bundled fixtures, through tools/run.py's real
functions, with provider=mock and a throwaway store. No network, no
credentials - the same path `make demo` and a real overnight run both take.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import load_settings
from core.review import approve
from core.store import Store
from tools import review, sync_book
from tools.run import _queue_proposal, one_pass_forecast, one_pass_parity, one_pass_repricing

TODAY = "2026-09-01"


def _store(tmp_path, monkeypatch):
    """Isolated settings: a real `config/agent.yaml` (a hotel's own room types,
    once they have set one up) must never change what these tests exercise -
    see the note in build-repo.md and docs/how-it-works.md. AGENT_CONFIG_DIR
    points load_settings() at fresh copies of the shipped examples instead.
    """
    monkeypatch.chdir(REPO_ROOT)
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "hotel.yaml").write_text(
        (REPO_ROOT / "config" / "hotel.example.yaml").read_text(encoding="utf-8"),
        encoding="utf-8")
    (cfg_dir / "agent.yaml").write_text(
        (REPO_ROOT / "config" / "agent.example.yaml").read_text(encoding="utf-8"),
        encoding="utf-8")
    monkeypatch.setenv("AGENT_CONFIG_DIR", str(cfg_dir))
    settings = load_settings(demo=True)
    store = Store(settings, path=tmp_path / "test.db")
    sync_book.migrate(store)
    return settings, store


def test_repricing_pass_produces_proposals_on_the_fixtures(tmp_path, monkeypatch):
    settings, store = _store(tmp_path, monkeypatch)
    code, stats = one_pass_repricing(settings, store, provider="mock", today=TODAY)
    store.close()
    assert code == 0
    assert stats["processed"] > 0
    assert stats["pending_review"] > 0  # near-term/deep-cut/MLOS holds always exist


def test_shadow_mode_never_calls_pms_set_rate(tmp_path, monkeypatch):
    settings, store = _store(tmp_path, monkeypatch)
    code, stats = one_pass_repricing(settings, store, provider="mock", today=TODAY)
    counts = store.counts()
    store.close()
    assert code == 0
    # Shadow blocks every write, including an auto-eligible one - nothing ever
    # reaches auto_sent. An "offer" has nothing to publish and is "skipped"
    # instead (see tools/run.py:_queue_proposal) - "auto_sent" means a write
    # actually happened, never "nothing to send."
    assert counts.get("auto_sent", 0) == 0
    assert stats.get("auto_sent", 0) == 0
    assert counts.get("skipped", 0) > 0  # the offers land here


def test_rerun_the_same_day_is_idempotent(tmp_path, monkeypatch):
    settings, store = _store(tmp_path, monkeypatch)
    one_pass_repricing(settings, store, provider="mock", today=TODAY)
    first_count = len(store.list_items(limit=1000))
    code, stats = one_pass_repricing(settings, store, provider="mock", today=TODAY)
    second_count = len(store.list_items(limit=1000))
    store.close()
    assert code == 0
    assert stats["processed"] == 0  # every cell already has a decision row today
    assert first_count == second_count


def test_a_new_day_gets_a_fresh_proposal_key(tmp_path, monkeypatch):
    settings, store = _store(tmp_path, monkeypatch)
    one_pass_repricing(settings, store, provider="mock", today=TODAY)
    first_count = len(store.list_items(limit=10000))
    one_pass_repricing(settings, store, provider="mock", today="2026-09-02")
    second_count = len(store.list_items(limit=10000))
    store.close()
    assert second_count > first_count  # tomorrow's run makes new decision rows


def test_approve_then_send_publishes_through_the_mock_pms(tmp_path, monkeypatch):
    from core.adapters import get_pms, get_sheets
    settings, store = _store(tmp_path, monkeypatch)
    one_pass_repricing(settings, store, provider="mock", today=TODAY)
    held = store.list_items(status="pending_review", kind="rate_move", limit=1)
    assert held, "expected at least one held rate proposal on the fixtures"
    item = held[0]
    approve(store, item.id)
    claimed = store.claim_for_send(limit=1)
    assert claimed and claimed[0].id == item.id
    settings.mode = "live"  # simulate a hotel that has gone live
    pms = get_pms(settings)
    result = pms.set_rate(item.draft["date"], item.draft["room_type_id"],
                          item.draft["proposed"], item=claimed[0])
    store.mark_sent(item.id, result.get("message_id"))
    updated = store.get_item(item.id)
    store.close()
    assert updated.review_status == "sent"


def test_forecast_pass_is_off_by_default(tmp_path, monkeypatch):
    settings, store = _store(tmp_path, monkeypatch)
    code, stats = one_pass_forecast(settings, store, provider="mock", today=TODAY)
    store.close()
    assert code == 0 and stats == {}


def test_forecast_pass_runs_when_enabled(tmp_path, monkeypatch):
    settings, store = _store(tmp_path, monkeypatch)
    settings.agent.setdefault("subagents", {}).setdefault("demand_forecasting", {})["enabled"] = True
    code, stats = one_pass_forecast(settings, store, provider="mock", today=TODAY)
    store.close()
    assert code == 0
    assert stats["summary"]["horizon_nights"] == settings.agent.get("horizon_nights", 21)


def test_weather_signal_toggle_changes_the_forecast(tmp_path, monkeypatch):
    """Regression for the MAJOR finding in SIMULATION.md: the real property a
    hotelier sets is subagents.demand_forecasting.weather_signal, a different
    dict than the top-level rules: block one_pass_forecast used to read -
    flipping it did nothing. workflows/21-demand-forecast.md step 5 promises
    the forecast "provably changes"; prove it both ways, through the real
    wiring (not just the pure project_occupancy() function)."""
    settings, store = _store(tmp_path, monkeypatch)
    demand = settings.agent.setdefault("subagents", {}).setdefault("demand_forecasting", {})
    demand["enabled"] = True
    demand["weather_signal"] = True
    _, stats_on = one_pass_forecast(settings, store, provider="mock", today=TODAY)
    demand["weather_signal"] = False
    _, stats_off = one_pass_forecast(settings, store, provider="mock", today=TODAY)
    store.close()
    assert stats_on["summary"] != stats_off["summary"]


def test_parity_pass_runs_when_enabled_and_finds_the_seeded_break(tmp_path, monkeypatch):
    settings, store = _store(tmp_path, monkeypatch)
    settings.agent.setdefault("subagents", {}).setdefault("ota_content_parity", {})["enabled"] = True
    sync_book.sync_nights(settings, store, horizon_nights=21, today=TODAY)
    code, stats = one_pass_parity(settings, store, today=TODAY)
    store.close()
    assert code == 0
    assert stats["processed"] > 0


def test_blocked_send_returns_item_to_approved_not_failed(tmp_path, monkeypatch, capsys):
    """Regression for the MINOR finding in SIMULATION.md, now fixed to match
    core's FSM (sending -> approved on a guard block): tools/review.py's
    cmd_send must never call mark_send_failed() for a WriteBlocked - that
    would land a shadow-mode block in `failed`, indistinguishable from a
    real error. The approval must stand, ready to publish once mode: live -
    see workflows/80-review.md steps 4-5."""
    from types import SimpleNamespace

    from tools.review import cmd_send

    settings, store = _store(tmp_path, monkeypatch)
    one_pass_repricing(settings, store, provider="mock", today=TODAY)
    held = store.list_items(status="pending_review", kind="rate_move", limit=1)
    assert held, "expected at least one held rate proposal on the fixtures"
    item = held[0]
    approve(store, item.id)
    assert settings.mode == "shadow"  # make demo (and _store, via demo=True) forces shadow

    cmd_send(store, settings, SimpleNamespace(limit=20))
    updated = store.get_item(item.id)
    store.close()

    out = capsys.readouterr().out
    assert "approval kept" in out
    assert updated.review_status == "approved"  # never "failed"
    assert not updated.error  # mark_send_failed() was never called


def test_queue_proposal_offer_never_touches_an_adapter(tmp_path, monkeypatch):
    from tools.pricing_engine import Proposal
    settings, store = _store(tmp_path, monkeypatch)
    prop = Proposal(date="2026-09-16", kind="offer", reason="test offer")
    stats = {"processed": 0, "skipped": 0, "auto_sent": 0, "pending_review": 0,
            "needs_human": 0}
    _queue_proposal(settings, store, pms=None, sheets=None, p=prop, run_date=TODAY, stats=stats)
    item = store.get_item(store.list_items(kind="offer", limit=1)[0].id)
    store.close()
    assert stats["skipped"] == 1  # never touched the (None) pms/sheets - would have raised
    assert item.review_status == "skipped"  # terminal, not auto_sent - nothing was sent


def test_interactive_provider_pauses_then_resumes_the_morning_note(tmp_path, monkeypatch):
    """A pause is not an error: LLMPendingInteractive must propagate all the way
    out as exit code 3, with every pricing decision already made and queued -
    never a silent fall-back to canned text. Writing the answer and re-running
    must pick it up.
    """
    import json as jsonlib

    from core.config import repo_root

    settings, store = _store(tmp_path, monkeypatch)
    pending_dir = repo_root() / "data" / "pending"
    prompt_path = pending_dir / "repricing_note-repricing-note-01.prompt.md"
    answer_path = pending_dir / "repricing_note-repricing-note-01.answer.json"
    for p in (prompt_path, answer_path, answer_path.with_suffix(".json.used")):
        p.unlink(missing_ok=True)
    try:
        code, stats = one_pass_repricing(settings, store, provider="interactive", today=TODAY)
        assert code == 3
        assert stats["processed"] > 0  # every rate decision was already made and queued
        assert prompt_path.exists()

        answer_path.write_text(jsonlib.dumps({"note": "Test note."}), encoding="utf-8")
        code2, stats2 = one_pass_repricing(settings, store, provider="interactive", today=TODAY)
        assert code2 == 0
        assert stats2["processed"] == 0  # today's decisions were already queued - idempotent
    finally:
        store.close()
        for p in (prompt_path, answer_path, answer_path.with_suffix(".json.used")):
            p.unlink(missing_ok=True)


def test_sample_item_shows_marker_in_list_line_and_show(tmp_path, monkeypatch, capsys):
    """core/store.py tags an item read through a mock adapter outside `make
    demo` as `_sample` (`Item.is_sample`) - a human working the real queue
    must see that at a glance, in both `list` and `show`."""
    settings, store = _store(tmp_path, monkeypatch)
    item = store.upsert_item("pms", "sample-marker-1", kind="rate_move",
                             payload={"date": TODAY, "room_type_id": "std",
                                      "current": 100, "proposed": 110, "_sample": True})
    assert item.is_sample

    capsys.readouterr()
    review._print_item_line(item)
    assert "[SAMPLE DATA]" in capsys.readouterr().out

    rc = review.cmd_show(store, SimpleNamespace(id=item.id))
    assert rc == 0
    assert "[SAMPLE DATA]" in capsys.readouterr().out
    store.close()
