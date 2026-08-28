#!/usr/bin/env python3
"""tools/report.py - what the agent did, and what it cost.

    make report
    python3 tools/report.py --json

Everything here is computed from `data/agent.db` - nothing phoned home. See
docs/benefits.md for what each number means and its honest caveats.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import ConfigError, load_settings  # noqa: E402
from core.review import queue_summary  # noqa: E402
from core.store import Store, StoreError  # noqa: E402


def build_report(store: Store) -> dict:
    counts = store.counts()
    queue = queue_summary(store)
    usage = store.usage_totals()
    total = sum(counts.values())
    auto = counts.get("auto_sent", 0)
    sent = counts.get("sent", 0) + auto
    edited = 0
    rejected = counts.get("rejected", 0)
    for row in store.db.execute("SELECT COUNT(DISTINCT source_item) AS n FROM learnings"
                                ).fetchall():
        edited = row["n"]
    auto_rate = round(100 * auto / total, 1) if total else 0.0
    kind_rows = store.db.execute(
        "SELECT kind, review_status, COUNT(*) AS n FROM items GROUP BY kind, review_status"
    ).fetchall()
    by_kind: dict[str, dict[str, int]] = {}
    for row in kind_rows:
        by_kind.setdefault(row["kind"], {})[row["review_status"]] = row["n"]
    return {
        "total_items": total, "by_status": counts, "by_kind": by_kind,
        "waiting_on_human": queue["waiting_on_human"], "auto_published_pct": auto_rate,
        "sent": sent, "rejected": rejected, "human_edits_recorded": edited,
        "llm_calls": usage["calls"], "llm_cost_usd": round(usage["cost_usd"], 4),
    }


def print_human(report: dict, mode: str) -> None:
    print("Revenue Management AI - report\n")
    print(f"Mode: {mode}")
    print(f"Total proposals seen: {report['total_items']}")
    print(f"Waiting for a person: {report['waiting_on_human']}")
    print(f"Published automatically: {report['sent']} ({report['auto_published_pct']}%)")
    print(f"Rejected: {report['rejected']}")
    print(f"Rate changed after a human edit: {report['human_edits_recorded']}")
    print()
    print("By kind:")
    for kind, statuses in sorted(report["by_kind"].items()):
        print(f"  {kind:<14} " + ", ".join(f"{s}={n}" for s, n in sorted(statuses.items())))
    print()
    print(f"LLM calls: {report['llm_calls']} (narration only - the model never sets a "
         f"price) - cost so far: ${report['llm_cost_usd']}")
    if mode == "shadow":
        print("\nNote: mode is shadow, so 'published automatically' counts what autopilot "
             "WOULD have published - see docs/benefits.md before quoting this number.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    try:
        store = Store(settings)
    except StoreError as exc:
        print(f"store error: {exc}", file=sys.stderr)
        return 1
    try:
        report = build_report(store)
    finally:
        store.close()

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_human(report, settings.mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
