#!/usr/bin/env python3
"""tools/review.py - work the review queue: list / show / approve / edit / reject / send.

    python3 tools/review.py list [--status pending_review] [--kind rate_move]
    python3 tools/review.py show <id>
    python3 tools/review.py approve <id> [--note "..."]
    python3 tools/review.py edit <id> --proposed 245           # a rate/gap_night proposal
    python3 tools/review.py edit <id> --proposed-mlos 2        # a stay-rule proposal
    python3 tools/review.py reject <id> --reason "too aggressive"
    python3 tools/review.py retry <id>          # re-queue a failed publish
    python3 tools/review.py stale                # go-live only: clear the shadow backlog
    python3 tools/review.py send                # publish everything approved/edited

Only this tool writes `approved` / `edited` / `rejected` (core/review.py). Only
`send` writes `sending` / `sent`. Nothing here bypasses `mode: shadow` - see
docs/safety.md.

Item kinds in this repo: `rate_move` (a rate or gap-night proposal, published
via `pms.set_rate()`), `mlos_change` (a stay-rule proposal, published via
`sheets.append()`), `offer` (informational, never sent), `ota_parity` and
`ota_content` (the Cartographer's findings - no channel adapter exists, so
`send` marks them applied and says so plainly; see docs/how-it-works.md).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters import get_pms, get_sheets  # noqa: E402
from core.adapters.base import AdapterError  # noqa: E402
from core.config import ConfigError, load_settings  # noqa: E402
from core.review import (WriteBlocked, approve, edit, list_queue, reject, retry, show,  # noqa: E402
                         stale_backlog)
from core.store import Store, StoreError  # noqa: E402
from tools import sync_book  # noqa: E402


def _print_item_line(item) -> None:
    p = item.payload or {}
    kind = item.kind
    if kind == "rate_move":
        what = f"{p.get('date','')} {p.get('room_type_id','')} {p.get('current')}->{p.get('proposed')}"
    elif kind == "mlos_change":
        what = f"{p.get('date','')} MLOS {p.get('current_mlos')}->{p.get('proposed_mlos')}"
    elif kind == "offer":
        what = f"{p.get('date','')} offer"
    elif kind in ("ota_parity", "ota_content"):
        what = f"{p.get('channel','')} {p.get('kind') or 'parity'}"
    else:
        what = json.dumps(p)[:50]
    # `item.is_sample` is set by core (core/store.py) for anything read
    # through a mock adapter outside `make demo` - see docs/integrations.md
    # "Sample data is labelled".
    marker = "  [SAMPLE DATA]" if item.is_sample else ""
    print(f"  {item.id}  {item.review_status:<14} {kind:<12} {what[:60]}{marker}")


def cmd_list(store, args) -> int:
    items = list_queue(store, status=args.status, kind=args.kind, limit=args.limit)
    if not items:
        print("Nothing is waiting for you.")
        return 0
    print(f"{len(items)} item(s) waiting:\n")
    for item in items:
        _print_item_line(item)
    print("\nRun `python3 tools/review.py show <id>` for the full draft and its reason.")
    return 0


def cmd_show(store, args) -> int:
    try:
        detail = show(store, args.id)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    item = detail["item"]
    draft = item.get("draft") or {}
    if (item.get("payload") or {}).get("_sample"):
        print("[SAMPLE DATA] this item was read through a mock adapter, not your "
             "property - see docs/integrations.md.\n")
    print(json.dumps(item, indent=2, ensure_ascii=False, default=str))
    if draft.get("reason"):
        print(f"\nWhy: {draft['reason']}")
    if draft.get("parts"):
        print("Contributions:")
        for part in draft["parts"]:
            print(f"  {part['label']:<28} {part['pct']:+.1f}%")
    if item["kind"] == "rate_move" and draft.get("proposed") is not None and draft.get("formula"):
        from tools.pricing_engine import price_response_curve
        curve = price_response_curve(draft["formula"], draft["proposed"],
                                     draft.get("current") or draft["formula"])
        print(f"\nPrice-response curve: optimal {curve['optimal_price']:g}, "
             f"current {curve['current_price']:g}, {len(curve['points'])} candidate "
             f"prices considered (see docs/how-it-works.md \"Design decisions\" #5).")
    if draft.get("fix_draft"):
        print(f"\nDraft fix:\n{draft['fix_draft']}")
    print("\nEvents:")
    for event in detail["events"]:
        print(f"  {event['ts']}  {event['actor']:<6} {event['action']}")
    return 0


def cmd_approve(store, args) -> int:
    item = approve(store, args.id, note=args.note or "")
    print(f"approved {item.id} - now in the send queue")
    return 0


def cmd_edit(store, args) -> int:
    item = store.get_item(args.id)
    if item is None:
        print(f"error: no item {args.id}", file=sys.stderr)
        return 1
    new_draft = dict(item.draft or item.payload or {})
    if args.proposed is not None:
        new_draft["proposed"] = args.proposed
    if args.proposed_mlos is not None:
        new_draft["proposed_mlos"] = args.proposed_mlos
    if args.proposed is None and args.proposed_mlos is None:
        print("error: give --proposed or --proposed-mlos", file=sys.stderr)
        return 1
    edit(store, args.id, new_draft, note=args.note or "")
    print(f"edited {item.id} - now in the send queue")
    return 0


def cmd_reject(store, args) -> int:
    item = reject(store, args.id, reason=args.reason or "")
    print(f"rejected {item.id}")
    return 0


def cmd_retry(store, args) -> int:
    item = retry(store, args.id)
    print(f"queued {item.id} for another publish attempt")
    return 0


def cmd_stale(store, args) -> int:
    """Run once at go-live (workflows/90-go-live.md): the queue built up during
    shadow mode was never published and is out of date by the time you trust
    the drafts. This clears it so nothing old goes out by surprise - approve a
    proposal again if it still matters."""
    ids = stale_backlog(store)
    print(f"{len(ids)} item(s) moved to stale.")
    return 0


def cmd_send(store, settings, args) -> int:
    claimed = store.claim_for_send(limit=args.limit)
    if not claimed:
        print("Nothing approved or edited is waiting to publish.")
        return 0
    pms = get_pms(settings)
    sheets = get_sheets(settings)
    sent, failed = 0, 0
    for item in claimed:
        draft = item.draft or item.payload or {}
        try:
            if item.kind == "rate_move":
                result = pms.set_rate(draft["date"], draft["room_type_id"], draft["proposed"],
                                      item=item)
                sync_book.publish_rate(store, draft["date"], draft["room_type_id"],
                                       draft["proposed"])
                message_id = result.get("message_id") if isinstance(result, dict) else None
            elif item.kind == "mlos_change":
                result = sheets.append("mlos_changes",
                                       [[draft["date"], draft.get("current_mlos"),
                                         draft["proposed_mlos"], draft.get("reason", "")]],
                                       item=item)
                sync_book.publish_mlos(store, draft["date"], draft["proposed_mlos"])
                message_id = result.get("message_id") if isinstance(result, dict) else None
            elif item.kind in ("ota_parity", "ota_content"):
                # No channel-manager adapter exists (see docs/how-it-works.md #1) -
                # the guard still runs; nothing external is actually called.
                from core.review import assert_write_allowed
                assert_write_allowed(settings, "publish", item)
                message_id = "simulated - no channel adapter connected"
            else:
                message_id = None
        except WriteBlocked as exc:
            # Not a failure: the mode blocked it. The approval stands for go-live -
            # never mark_send_failed() here, that would lose the human's decision.
            store.transition(item.id, "approved", "agent", {"blocked": str(exc)[:200]})
            print(f"blocked {item.id} (approval kept): {exc}")
            failed += 1
            continue
        except Exception as exc:  # noqa: BLE001 - record and move on, never crash the batch
            store.mark_send_failed(item.id, str(exc))
            print(f"failed {item.id}: {exc}")
            failed += 1
            continue
        store.mark_sent(item.id, message_id)
        print(f"sent {item.id}")
        sent += 1
    print(f"\n{sent} sent, {failed} failed.")
    return 0 if failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="what is waiting for a human")
    p_list.add_argument("--status", default=None)
    p_list.add_argument("--kind", default=None)
    p_list.add_argument("--limit", type=int, default=50)

    p_show = sub.add_parser("show", help="full detail for one item")
    p_show.add_argument("id")

    p_approve = sub.add_parser("approve", help="approve the proposal unchanged")
    p_approve.add_argument("id")
    p_approve.add_argument("--note", default="")

    p_edit = sub.add_parser("edit", help="change the number, then queue it")
    p_edit.add_argument("id")
    p_edit.add_argument("--proposed", type=float, default=None, help="override the rate")
    p_edit.add_argument("--proposed-mlos", type=int, default=None,
                        help="override the stay-rule minimum")
    p_edit.add_argument("--note", default="")

    p_reject = sub.add_parser("reject", help="discard the proposal")
    p_reject.add_argument("id")
    p_reject.add_argument("--reason", default="")

    p_retry = sub.add_parser("retry", help="re-queue a failed publish")
    p_retry.add_argument("id")

    sub.add_parser("stale", help="go-live only: clear the shadow-mode backlog")

    p_send = sub.add_parser("send", help="publish everything approved or edited")
    p_send.add_argument("--limit", type=int, default=20)

    args = parser.parse_args(argv)

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
    sync_book.migrate(store)
    try:
        if args.command == "list":
            return cmd_list(store, args)
        if args.command == "show":
            return cmd_show(store, args)
        if args.command == "approve":
            return cmd_approve(store, args)
        if args.command == "edit":
            return cmd_edit(store, args)
        if args.command == "reject":
            return cmd_reject(store, args)
        if args.command == "retry":
            return cmd_retry(store, args)
        if args.command == "stale":
            return cmd_stale(store, args)
        if args.command == "send":
            return cmd_send(store, settings, args)
        parser.error(f"unknown command {args.command}")
        return 2
    except (AdapterError, StoreError, WriteBlocked) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
