# Workflow: the repricing loop

Objective: sync the live book, run the pricing engine, publish what
guardrails allow, and queue everything else for a human. This is Revenue
Management AI's main job — the roster's "twice-hourly schedule."

## Steps

1. **Check the agent is healthy.**
   ```bash
   make doctor
   ```
   A `FAIL` on "room types" means `config/agent.yaml` is missing a floor or a
   ceiling for one of your room types — fix that before running the engine;
   it will not price a room type it does not have guardrails for.

2. **Run one pass.**
   ```bash
   make run                        # sync + reprice + classify + publish/queue
   make run ARGS="--dry-run"       # compute everything, publish nothing
   make run ARGS="--as-of 2026-09-01"   # rehearse against a specific date
   ```
   If `llm.provider` is `interactive`, the pass finishes the real work first
   (nothing about pricing waits on the model) and then stops with exit code 3
   while it waits for you to write the morning note. Read
   `data/pending/*.prompt.md`, answer into the matching `*.answer.json`, and
   re-run the same command — the pricing decisions already made are not
   recomputed. (That "3" is `tools/run.py`'s own exit code. Through
   `make run`, the console prints `make: *** [run] Error 3` — but Make's own
   exit status is always 2 for any failed recipe, not 3, whatever the
   command underneath actually returned; see `workflows/99-troubleshooting.md`.)

3. **Read what it did.** Every pass prints its own thinking log, one line per
   step: the live book, pickup pace, the competitor scan, the event radar,
   gap nights, stay rules, the draft proposals, the guardrail check, and the
   headline decision. Summarise this for the user in plain language — how
   many moves, the biggest reason, what got held.

4. **Show what is waiting.**
   ```bash
   make review
   python3 tools/review.py show <id>
   ```
   `show` prints the reason, the contribution breakdown (`parts`), and for a
   rate move, a summary of the price-response curve. Do not paste raw JSON
   at the user — read the reason and the numbers back to them.

5. **Act on their decision.**
   ```bash
   python3 tools/review.py approve <id>
   python3 tools/review.py edit <id> --proposed 245        # override a rate
   python3 tools/review.py edit <id> --proposed-mlos 2     # override a stay rule
   python3 tools/review.py reject <id> --reason "too aggressive this week"
   ```
   Then publish what was approved:
   ```bash
   python3 tools/review.py send
   ```
   In `mode: shadow` this always reports "blocked... nothing leaves in
   shadow mode" — that is the point. Nothing publishes until
   `workflows/90-go-live.md` has been worked through.

6. **Report.**
   ```bash
   make report
   ```

## What "auto-eligible" actually means today

On a fresh clone, `autopilot: guarded` in `config/agent.yaml` decides which
proposals the engine is *willing* to publish itself — but `mode: shadow` in
`config/hotel.yaml` is the global kill switch, and it blocks every publish
attempt regardless, recording the attempt as `pending_review` instead. An
auto-eligible proposal only really auto-publishes once `mode: live` (see
`workflows/90-go-live.md`) — until then, "guarded autopilot" and "advise
mode" look the same from the outside: everything waits for you. That is
deliberate, not a bug.

## Edge cases

- **A proposal you already decided today does not come back.** Proposals are
  keyed per calendar day (`docs/how-it-works.md` "Idempotency") — re-running
  the same day refreshes nothing for a cell that already has a decision.
  Tomorrow's run gets a fresh key.
- **A held item you never look at.** `store.mark_stale()` runs every pass and
  ages anything sitting in `pending_review`/`needs_human` for more than 72
  hours to `stale`. Revive it with `python3 tools/review.py show <id>` — a
  human can move a `stale` item back with `approve`/`edit`/`reject`.
- **A publish fails partway.** `python3 tools/review.py show <id>` shows
  exactly why — a blocked write (shadow mode, or the action still needs
  approval) queues the item back to `pending_review`; a real adapter error
  moves it to `failed`, and `python3 tools/review.py retry <id>` re-queues it.
