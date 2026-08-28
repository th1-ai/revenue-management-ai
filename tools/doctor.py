#!/usr/bin/env python3
"""tools/doctor.py - is Revenue Management AI configured and reachable right now?

    make doctor
    python3 tools/doctor.py

Runs the generic core.doctor checks (python, deps, config, .env, hotel
identity, mode, llm provider, every adapter, the store, knowledge) plus this
agent's own: room types configured with a floor/ceiling, `engine: simple`'s
reference_room_type (must be one of those room types), the one knowledge
file this agent reads, the prompts, and which signal each of
pace/comp-rates/events/OTA data is actually reading from (a CSV you
supplied, or the demo fixtures). Exits 0 when everything passed, 1 when a
FAIL line needs fixing. Never a traceback.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import ConfigError, Settings, load_settings  # noqa: E402
from core.doctor import Check, FAIL, PASS, WARN, print_table, run_checks  # noqa: E402
from tools import ingest, pricing_engine  # noqa: E402


def check_room_types(settings: Settings) -> Check:
    room_types = settings.agent_get("room_types", {})
    if not room_types:
        return Check("room types", FAIL, "no room_types in config/agent.yaml",
                     "Copy config/agent.example.yaml to config/agent.yaml and list your "
                     "own room types, base rates, floors and ceilings.")
    bad = [rt_id for rt_id, fields in room_types.items()
          if not (fields or {}).get("base_rate") or fields.get("floor") is None
          or fields.get("ceiling") is None or fields["floor"] >= fields["ceiling"]]
    if bad:
        return Check("room types", FAIL,
                     f"{len(bad)} room type(s) missing a base rate or a sane floor/ceiling: "
                     f"{', '.join(bad)}",
                     "Every room type needs base_rate, floor < ceiling in config/agent.yaml.")
    return Check("room types", PASS, f"{len(room_types)}: {', '.join(room_types)}")


def check_simple_engine(settings: Settings) -> Check:
    """``engine: simple`` needs ``simple.reference_room_type`` to name one of
    ``room_types`` above - the whole simple-mode counter-offer is priced off
    that one room. Runs the exact same check ``run_simple_pricing`` uses
    (`tools/pricing_engine.py:simple_reference_room_type`), so a hotel that
    renames its room types without updating this finds out here, not from a
    traceback through `make run`."""
    cfg = settings.agent
    if cfg.get("engine", "full") != "simple":
        return Check("simple engine", PASS, "engine: full - reference_room_type not used")
    try:
        ref_id = pricing_engine.simple_reference_room_type(cfg)
    except pricing_engine.SimpleEngineConfigError as exc:
        return Check("simple engine", FAIL, str(exc),
                     "Set simple.reference_room_type in config/agent.yaml to one of "
                     "the ids listed under room_types.")
    return Check("simple engine", PASS, f"reference_room_type: {ref_id}")


def check_pricing_policy() -> Check:
    """This agent reads exactly one knowledge file - `pricing-policy.md` - see
    `knowledge/README.md`. The generic `core.doctor.check_knowledge()` above
    only checks that *some* non-example knowledge/*.md file exists, which
    would PASS even if a hotelier filled in the generic `property.md`
    scaffold (never read by this agent) and never created the one file that
    matters. This check names that file specifically."""
    path = REPO_ROOT / "knowledge" / "pricing-policy.md"
    if not path.is_file():
        return Check("pricing policy", WARN, "knowledge/pricing-policy.md not created yet",
                     "cp knowledge/pricing-policy.example.md knowledge/pricing-policy.md, "
                     "then fill in your own floor/ceiling reasoning - see "
                     "knowledge/README.md. The other knowledge/*.md files are generic "
                     "scaffolding this agent never reads.")
    return Check("pricing policy", PASS, "knowledge/pricing-policy.md present")


def check_rules(settings: Settings) -> Check:
    rules = settings.agent_get("rules", {})
    off = [k for k, v in rules.items() if not v]
    return Check("pricing rules", PASS,
                 f"{len(rules)} rule(s)" + (f", off: {', '.join(off)}" if off else ", all on"))


def check_prompts() -> Check:
    missing = [p for p in ("prompts/repricing_note.md", "prompts/forecast_note.md",
                           "prompts/schemas/repricing_note.json",
                           "prompts/schemas/forecast_note.json")
              if not (REPO_ROOT / p).is_file()]
    if missing:
        return Check("prompts", FAIL, f"missing {', '.join(missing)}",
                     "These ship with the repo - restore them from git.")
    return Check("prompts", PASS, "repricing_note.md + forecast_note.md + schemas present")


def check_signals() -> Check:
    sources = ingest.sources_used()
    none_configured = [k for k, v in sources.items() if v.startswith("none")]
    detail = "; ".join(f"{k}: {v}" for k, v in sources.items())
    if none_configured:
        return Check("signal sources", WARN, detail,
                     "No data/imports/*.csv or fixtures/inbound/*.json for "
                     f"{', '.join(none_configured)} - the engine will treat them as "
                     "neutral/empty. See docs/integrations.md.")
    return Check("signal sources", PASS, detail)


def check_subagents(settings: Settings) -> Check:
    oracle = settings.agent_get("subagents.demand_forecasting.enabled", False)
    cartographer = settings.agent_get("subagents.ota_content_parity.enabled", False)
    return Check("sub-agents", PASS,
                 f"Demand Forecasting AI {'on' if oracle else 'off'}, "
                 f"OTA Content & Parity AI {'on' if cartographer else 'off'}")


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        checks = run_checks(None) + [Check("config", FAIL, str(exc),
                                           "Fix config/hotel.yaml or config/agent.yaml.")]
        return print_table(checks, title="Revenue Management AI - doctor")

    checks = run_checks(settings, extra=[check_room_types, check_simple_engine, check_rules,
                                        check_subagents])
    checks.append(check_prompts())
    checks.append(check_signals())
    checks.append(check_pricing_policy())
    return print_table(checks, title="Revenue Management AI - doctor")


if __name__ == "__main__":
    raise SystemExit(main())
