#!/usr/bin/env python3
"""tools/demo.py - the whole loop on the bundled fixtures, zero credentials.

    make demo
    python3 tools/demo.py

Uses `load_settings(demo=True)`: mock provider, shadow mode, and the mock
adapter for every system, whatever config/hotel.yaml says - a demo can never
read a real mailbox or PMS. Runs against its own database
(`data/demo/demo.db`) so running it twice always shows the same picture and
never touches `data/agent.db` (that is `make run`'s file). Both sub-agents
are force-enabled for this walkthrough only, so a fresh clone sees all three
loops without editing config first - in a real run they stay off until you
turn them on (`config/agent.yaml: subagents.*.enabled`).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import ConfigError, load_settings, sub_data_dir  # noqa: E402
from core.log import summary_line  # noqa: E402
from core.store import Store, StoreError  # noqa: E402
from tools import sync_book  # noqa: E402
from tools.run import one_pass_forecast, one_pass_parity, one_pass_repricing  # noqa: E402

# Fixed so the demo never depends on the real wall-clock date - fixtures/hotel and
# fixtures/inbound are all dated around this anchor. Real runs use date.today().
DEMO_TODAY = "2026-09-01"


def main() -> int:
    try:
        settings = load_settings(demo=True)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1
    settings.agent.setdefault("subagents", {})
    settings.agent["subagents"].setdefault("demand_forecasting", {})["enabled"] = True
    settings.agent["subagents"].setdefault("ota_content_parity", {})["enabled"] = True

    demo_db = sub_data_dir("demo") / "demo.db"
    if demo_db.exists():
        demo_db.unlink()  # every `make demo` is a clean, repeatable run
    try:
        store = Store(settings, path=demo_db)
    except StoreError as exc:
        print(f"store error: {exc}", file=sys.stderr)
        return 1
    sync_book.migrate(store)

    print("Revenue Management AI demo - Hotel Aurora, fixtures/hotel + fixtures/inbound\n")
    print("Repricing (tools/run.py):\n")
    code, stats = one_pass_repricing(settings, store, provider="mock", today=DEMO_TODAY)
    if code != 0:
        print("demo: repricing pass did not finish cleanly", file=sys.stderr)
        return 1

    print("\nDemand Forecasting AI - The Oracle (tools/run.py --forecast):\n")
    fcode, fstats = one_pass_forecast(settings, store, provider="mock", today=DEMO_TODAY)
    if fcode != 0:
        print("demo: forecast pass did not finish cleanly", file=sys.stderr)
        return 1

    print("\nOTA Content & Parity AI - The Cartographer (tools/run.py --parity):\n")
    pcode, pstats = one_pass_parity(settings, store, today=DEMO_TODAY)
    if pcode != 0:
        print("demo: parity pass did not finish cleanly", file=sys.stderr)
        return 1

    counts = store.counts()
    waiting = sum(counts.get(s, 0) for s in ("pending_review", "needs_human"))
    print(f"\n{waiting} item(s) waiting for a person "
         f"(deep cuts, stay-rule changes and near-term nights always do - see "
         f"docs/safety.md).")
    print("Nothing was published: mode is shadow, and demo never calls set_rate() or "
         "sheets.append() on anything but the fixtures.")
    print("Next: `make review` to see what is waiting, or read workflows/10-repricing.md.\n")

    demo_stats = {"processed": stats.get("processed", 0), "drafted": stats.get("processed", 0),
                 "sent": stats.get("auto_sent", 0)}
    print(f"DEMO OK — {summary_line(demo_stats, settings.mode)}")
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
