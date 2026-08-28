#!/usr/bin/env python3
"""tools/run.py - Revenue Management AI's main loop: sync -> reprice -> classify
-> queue/publish -> narrate.

    python3 tools/run.py --once
    python3 tools/run.py --watch
    python3 tools/run.py --once --dry-run
    python3 tools/run.py --once --provider mock
    python3 tools/run.py --once --forecast     # Demand Forecasting AI pass
    python3 tools/run.py --once --parity       # OTA Content & Parity AI pass

The repricing engine (`tools/pricing_engine.py`) never sends anything on its
own. An auto-eligible proposal (guarded/full autopilot, inside every
guardrail) is published in this same pass, still through the write guard -
shadow mode and an empty review.require_approval_for blocks that regardless.
Everything else waits in `workflows/80-review.md`'s queue.

Exit codes: 0 ok, 3 waiting on an `interactive` narrative answer, 1 a real error.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters import get_pms, get_sheets  # noqa: E402
from core.adapters.base import AdapterError, AdapterNotImplemented  # noqa: E402
from core.config import ConfigError, load_settings  # noqa: E402
from core.llm import LLMError, LLMPendingInteractive, complete  # noqa: E402
from core.log import Run, get_logger, summary_line  # noqa: E402
from core.review import WriteBlocked  # noqa: E402
from core.store import Store, StoreError  # noqa: E402
from core.templates import build_prompt  # noqa: E402
from tools import ingest, sync_book  # noqa: E402
from tools.pricing_engine import (SimpleEngineConfigError, classify_proposals,
                                  room_types_from_cfg, run_repricing,
                                  run_simple_pricing)  # noqa: E402
from tools.forecast_engine import run_forecast  # noqa: E402
from tools.parity_engine import ContentFinding, run_channel_sweep  # noqa: E402

log = get_logger("run")


def _proposal_kind_for_item(kind: str) -> str:
    return {"rate": "rate_move", "gap_night": "rate_move", "mlos": "mlos_change",
           "offer": "offer"}.get(kind, kind)


def _proposal_dict(p) -> dict:
    return {"date": p.date, "room_type_id": p.room_type_id, "kind": p.kind,
           "current": p.current, "proposed": p.proposed, "formula": p.formula,
           "current_mlos": p.current_mlos, "proposed_mlos": p.proposed_mlos,
           "delta_pct": p.delta_pct, "reason": p.reason, "parts": p.parts}


def _publish(settings, store, pms, sheets, item) -> str | None:
    """Attempt the real write for one auto-eligible item. Returns a message id or None."""
    p = item.payload
    if p["kind"] in ("rate", "gap_night"):
        result = pms.set_rate(p["date"], p["room_type_id"], p["proposed"], item=item)
        sync_book.publish_rate(store, p["date"], p["room_type_id"], p["proposed"])
        return result.get("message_id") if isinstance(result, dict) else None
    if p["kind"] == "mlos":
        result = sheets.append("mlos_changes",
                               [[p["date"], p["current_mlos"], p["proposed_mlos"],
                                 p["reason"]]], item=item)
        sync_book.publish_mlos(store, p["date"], p["proposed_mlos"])
        return result.get("message_id") if isinstance(result, dict) else None
    return None


def _queue_proposal(settings, store, pms, sheets, p, run_date: str, stats: dict) -> None:
    payload = _proposal_dict(p)
    item, created = store.upsert_unique(_proposal_kind_for_item(p.kind),
                                        p.unique_key(run_date), payload,
                                        source="pricing_engine")
    if not created:
        stats["skipped"] += 1
        return
    stats["processed"] += 1
    store.set_fields(item.id, draft=payload)

    if p.kind == "offer":
        # Nothing to publish - no PMS/Sheets write exists for a length-of-stay
        # offer. Terminal from "new" directly (the FSM has no dispatched ->
        # skipped edge), and counted under "skipped" so the run summary's
        # "sent" figure only ever reflects a write that actually happened.
        store.transition(item.id, "skipped", "agent",
                         {"reason": "informational only, nothing to send"})
        stats["skipped"] += 1
        return

    store.transition(item.id, "dispatched", "agent")
    if p.auto:
        try:
            message_id = _publish(settings, store, pms, sheets, item)
            if message_id:
                store.set_fields(item.id, sent_message_id=message_id)
            store.transition(item.id, "auto_sent", "agent", {"published": True})
            stats["auto_sent"] += 1
            return
        except WriteBlocked as exc:
            store.transition(item.id, "pending_review", "agent",
                             {"blocked": str(exc)})
            stats["pending_review"] += 1
            return
        except AdapterNotImplemented as exc:
            store.transition(item.id, "needs_human", "agent", {"error": str(exc)[:300]})
            stats["needs_human"] += 1
            return
        except Exception as exc:  # noqa: BLE001 - record and move on, never crash the batch
            store.set_fields(item.id, error=str(exc)[:1000])
            store.transition(item.id, "failed", "agent", {"error": str(exc)[:300]})
            stats["needs_human"] += 1
            return
    reason = p.hold_reason or "waiting for review"
    store.transition(item.id, "pending_review", "agent", {"hold_reason": reason})
    stats["pending_review"] += 1


def _narrate(task: str, fixture_id: str, item: dict, settings, store, provider: str | None):
    """Best-effort: a narration failure never fails a run that already succeeded.

    Under `--dry-run`, `store=None` is passed to `complete()` so it cannot log
    the usage event - a rehearsal writes nothing, not even an audit row.
    """
    schema = json.loads(
        (REPO_ROOT / "prompts" / "schemas" / f"{task}.json").read_text(encoding="utf-8"))
    prompt = build_prompt(task, settings=settings, item=item, fixture_id=fixture_id)
    try:
        result = complete(task, prompt, schema, settings=settings, provider=provider,
                          store=None if settings.dry_run else store)
        return (result.data or {}).get("note")
    except LLMPendingInteractive:
        raise
    except LLMError as exc:
        log.warn(f"{task} skipped", error=str(exc)[:200])
        return None


def one_pass_repricing(settings, store, *, provider: str | None, today: str | None = None
                       ) -> tuple[int, dict]:
    stats = {"processed": 0, "skipped": 0, "auto_sent": 0, "pending_review": 0,
            "needs_human": 0, "sent": 0}
    cfg = settings.agent
    rules = cfg.get("rules", {})
    horizon = cfg.get("horizon_nights", 21)
    today = today or date.today().isoformat()

    with Run("repricing", settings, None if settings.dry_run else store) as run:
        sync_book.sync_nights(settings, store, horizon_nights=horizon, today=today)
        nights = sync_book.load_nights(settings, store, today=today, horizon_nights=horizon)
        comps = ingest.load_comp_rates()
        events = ingest.load_events()

        if cfg.get("engine", "full") == "simple":
            result = run_simple_pricing(nights, comps, cfg, today)
            for p in result.proposals:
                p.hold_reason = "Simple mode always waits for review."
        else:
            result = run_repricing(nights, comps, events, rules, cfg, today)
            classify_proposals(result.proposals, nights, comps, rules, cfg,
                              cfg.get("autopilot", "guarded"), today)

        if settings.dry_run:
            # --dry-run writes nothing: no item rows, no nights cache, no run row.
            stats["processed"] = len(result.proposals)
        else:
            pms = get_pms(settings)
            sheets = get_sheets(settings)
            for p in result.proposals:
                _queue_proposal(settings, store, pms, sheets, p, today, stats)

        reaped = store.reap_stuck_sending() if not settings.dry_run else []
        stale = store.mark_stale(72) if not settings.dry_run else []
        if reaped:
            log.warn("reaped stuck sends", count=len(reaped))
        if stale:
            log.info("marked stale", count=len(stale))

        stats["drafted"] = stats["processed"]
        run.stats = {**stats, "summary": result.summary}

        try:
            note = _narrate("repricing_note", "repricing-note-01",
                           {"summary": result.summary,
                            "sample_proposals": [_proposal_dict(p)
                                                 for p in result.proposals[:12]]},
                           settings, store, provider)
            if note:
                print(f"\nNote: {note}\n")
        except LLMPendingInteractive as exc:
            print(str(exc))
            return 3, stats

    for line in result.thinking_log:
        print(f"  - {line}")
    return 0, stats


def one_pass_forecast(settings, store, *, provider: str | None,
                      today: str | None = None) -> tuple[int, dict]:
    if not settings.agent_get("subagents.demand_forecasting.enabled", False):
        print("Demand Forecasting AI is off - enable subagents.demand_forecasting.enabled "
             "in config/agent.yaml. See workflows/21-demand-forecast.md.")
        return 0, {}
    cfg = settings.agent
    # subagents.demand_forecasting.weather_signal, not the top-level rules:
    # block - that's the actual property a hotelier sets (see
    # workflows/21-demand-forecast.md step 1). Merged in here the same way
    # one_pass_parity below merges subagents.ota_content_parity.content_sync,
    # so project_occupancy()'s rules.get("weather_signal", True) actually
    # reads what was toggled instead of always defaulting to True.
    df_cfg = cfg.get("subagents", {}).get("demand_forecasting", {})
    rules = {**cfg.get("rules", {}), "weather_signal": df_cfg.get("weather_signal", True)}
    today = today or date.today().isoformat()
    with Run("demand_forecast", settings, None if settings.dry_run else store) as run:
        sync_book.sync_nights(settings, store, horizon_nights=cfg.get("horizon_nights", 21),
                              today=today)
        nights = sync_book.load_nights(settings, store, today=today,
                                       horizon_nights=cfg.get("horizon_nights", 21))
        comps = ingest.load_comp_rates()
        events = ingest.load_events()
        result = run_forecast(nights, comps, events, rules, cfg, today)
        run.stats = {"summary": result.summary}
        try:
            note = _narrate("forecast_note", "forecast-note-01",
                           {"summary": result.summary, "rate_shopping": result.callouts},
                           settings, store, provider)
            if note:
                print(f"\nNote: {note}\n")
        except LLMPendingInteractive as exc:
            print(str(exc))
            return 3, {}
    for line in result.thinking_log:
        print(f"  - {line}")
    return 0, {"summary": result.summary}


def one_pass_parity(settings, store, *, today: str | None = None) -> tuple[int, dict]:
    if not settings.agent_get("subagents.ota_content_parity.enabled", False):
        print("OTA Content & Parity AI is off - enable subagents.ota_content_parity.enabled "
             "in config/agent.yaml. See workflows/22-ota-parity.md.")
        return 0, {}
    cfg = settings.agent
    rules = cfg.get("rules", {})
    today = today or date.today().isoformat()
    channels = cfg.get("subagents", {}).get("ota_content_parity", {}).get(
        "channels", ["Booking.com", "Expedia", "Google Hotel Ads", "Airbnb"])
    stats = {"processed": 0, "skipped": 0, "pending_review": 0}
    with Run("ota_parity", settings, None if settings.dry_run else store) as run:
        nights = sync_book.load_nights(settings, store, today=today,
                                       horizon_nights=cfg.get("horizon_nights", 21))
        room_types = room_types_from_cfg(cfg)
        ota_rates = ingest.load_ota_rates()
        findings = [ContentFinding(**row) for row in ingest.load_ota_content_findings()]
        oc_cfg = cfg.get("subagents", {}).get("ota_content_parity", {})
        parity_cfg = {**cfg, "parity_tolerance_pct": oc_cfg.get("parity_tolerance_pct", 0.01)}
        from tools.parity_engine import draft_content_fix, draft_parity_fix
        result = run_channel_sweep(nights, room_types, ota_rates, findings, channels,
                                   {**rules, "content_sync": oc_cfg.get("content_sync", True)},
                                   parity_cfg)
        if settings.dry_run:
            # --dry-run writes nothing: no item rows, no run row.
            stats["processed"] = len(result.parity_breaks) + len(findings)
        else:
            for brk in result.parity_breaks:
                payload = {"channel": brk.channel, "date": brk.date,
                          "room_type_id": brk.room_type_id, "direct_rate": brk.direct_rate,
                          "observed_rate": brk.observed_rate, "pct_under": brk.pct_under,
                          "fix_draft": draft_parity_fix(brk)}
                item, created = store.upsert_unique("ota_parity", brk.unique_key(today), payload,
                                                    source="ota_parity")
                if not created:
                    stats["skipped"] += 1
                    continue
                stats["processed"] += 1
                store.set_fields(item.id, draft=payload)
                store.transition(item.id, "dispatched", "agent")
                store.transition(item.id, "pending_review", "agent")
                stats["pending_review"] += 1
            for finding in findings:
                payload = {"channel": finding.channel, "kind": finding.kind,
                          "detail": finding.detail, "severity": finding.severity,
                          "fix_draft": draft_content_fix(finding)}
                item, created = store.upsert_unique("ota_content", finding.unique_key(today),
                                                    payload, source="ota_parity")
                if not created:
                    stats["skipped"] += 1
                    continue
                stats["processed"] += 1
                store.set_fields(item.id, draft=payload)
                store.transition(item.id, "dispatched", "agent")
                store.transition(item.id, "pending_review", "agent")
                stats["pending_review"] += 1
        run.stats = {**stats, "summary": result.summary}
    for line in result.thinking_log:
        print(f"  - {line}")
    return 0, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--once", action="store_true", help="run a single pass (default)")
    mode_group.add_argument("--watch", action="store_true",
                            help="keep running on the configured interval")
    parser.add_argument("--forecast", action="store_true",
                        help="run the Demand Forecasting AI pass instead of repricing")
    parser.add_argument("--parity", action="store_true",
                        help="run the OTA Content & Parity AI pass instead of repricing")
    parser.add_argument("--dry-run", action="store_true",
                        help="compute everything, write nothing, even in live mode")
    parser.add_argument("--provider", default=None, help="override llm.provider for this run")
    parser.add_argument("--as-of", default=None,
                        help="override today's date (YYYY-MM-DD) - mainly for tests/demo")
    parser.add_argument("--poll-seconds", type=int, default=None,
                        help="override the --watch interval")
    args = parser.parse_args(argv)

    try:
        settings = load_settings(provider=args.provider, dry_run=args.dry_run)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    try:
        store = Store(settings)
    except StoreError as exc:
        print(f"store error: {exc}", file=sys.stderr)
        return 1
    sync_book.migrate(store)
    try:
        def pass_fn():
            if args.forecast:
                return one_pass_forecast(settings, store, provider=args.provider,
                                         today=args.as_of)
            if args.parity:
                return one_pass_parity(settings, store, today=args.as_of)
            return one_pass_repricing(settings, store, provider=args.provider,
                                      today=args.as_of)

        if args.watch:
            poll_seconds = args.poll_seconds or int(settings.agent_get("poll_seconds", 1800))
            while True:
                code, stats = pass_fn()
                print(summary_line({"processed": stats.get("processed", 0),
                                    "drafted": stats.get("processed", 0),
                                    "sent": stats.get("auto_sent", 0)}, settings.mode))
                if code != 0:
                    return code
                time.sleep(poll_seconds)
        code, stats = pass_fn()
        print(summary_line({"processed": stats.get("processed", 0),
                            "drafted": stats.get("processed", 0),
                            "sent": stats.get("auto_sent", 0)}, settings.mode))
        return code
    except (LLMError, AdapterError, StoreError, WriteBlocked,
           SimpleEngineConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
