# Workflow: troubleshooting

Read the whole error before doing anything - every tool here is written to
say what broke and what to do about it. If you fix something not covered
below, add it here.

## `make doctor` shows a FAIL

Each `FAIL` line has a `->` fix hint right under it. Common ones:

- **`hotel identity`: name is still 'Hotel Aurora'.** Expected on a fresh
  clone. Edit `config/hotel.yaml`.
- **`knowledge` warns "only example files" and points at `property.md`.**
  That hint is shared, generic core wording — for this agent specifically,
  ignore the mention of `property.md`/`faq.md` (this agent reads neither)
  and copy `knowledge/pricing-policy.example.md` instead; see
  `knowledge/README.md`.
- **`email adapter` / `messaging adapter` show `ok` even though this agent
  never sends an email or a message.** `make doctor` checks all four system
  families every repo in this factory shares — this agent genuinely only
  uses `pms` and `sheets` (see `docs/integrations.md`); the other two rows
  are informational, not a sign anything is misconfigured.
- **`room types`: no room_types in config/agent.yaml, or one is missing a
  floor/ceiling.** Copy `config/agent.example.yaml` to `config/agent.yaml`
  and replace the sample room types with your own — every one needs
  `base_rate`, `floor` and `ceiling`, and `floor` must be below `ceiling`.
- **`llm provider`: claude-code selected but `claude` is not on PATH.**
  Install Claude Code, or switch `llm.provider` to `interactive` or
  `anthropic` in `config/hotel.yaml`.
- **`llm provider`: ANTHROPIC_API_KEY is not set.** Add it to `.env`, or
  switch `llm.provider` to `claude-code` or `interactive`.
- **`signal sources` warns "none" for one of pace/comp_rates/events/
  ota_rates/ota_content_findings.** Not a failure — the engine treats a
  missing signal as neutral (pace 0, comp median 1.0) rather than guessing.
  Add `data/imports/<name>.csv` when you have the data; see
  `docs/integrations.md`.
- **An adapter shows FAIL, not warn.** `universal`/`built` adapters fail loud
  when misconfigured (a `warn` is reserved for stubs). Read the `detail`
  column — it names the missing file or variable.

## `make demo` does not print `DEMO OK`

- Make sure `make setup` ran first (`.venv` must exist).
- `tools/demo.py` forces `llm.provider=mock`, `mode=shadow`, and a fixed
  `--as-of 2026-09-01` — it never depends on today's real date. It reads
  `fixtures/hotel/reservations.json`, `fixtures/hotel/room_types.json` and
  every `fixtures/inbound/*.json`. If you deleted or renamed one, restore it
  from git.
- Read the traceback if there is one; `tools/demo.py` does not swallow
  errors on purpose, so a fixture problem shows up immediately.

## `make run` exits with code 3

Not an error. `llm.provider: interactive` parked the morning-note prompt —
every pricing decision was already made and queued before this happened.
Read `data/pending/*.prompt.md`, write your answer to the matching
`*.answer.json` (JSON only, matching the schema shown, no prose, no code
fence), and run the same command again.

**Note on the "3":** that is `python3 tools/run.py --once`'s own exit code.
Through `make run`, the console line reads `make: *** [run] Error 3` — Make
names the real code there — but Make's own process exit status (what a
script sees as `$?`) is always **2** for any failed recipe, whatever number
the recipe actually returned; that is GNU Make's own convention, not
something this agent controls. If you are scripting against the exit code
(to detect "3 = pending" vs "1 = real error"), call
`python3 tools/run.py --once` directly instead of going through `make run`.

## A proposal never appears in the queue

- Check it is not simply already there: proposals are keyed per calendar day
  (`docs/how-it-works.md` "Idempotency") — re-running the same day for a
  cell that already has a decision changes nothing.
- A move under `min_move_eur` is deliberately discarded as noise before it
  ever becomes a proposal — check `python3 tools/run.py --once --dry-run` and
  read the thinking log's "Draft rate proposals" line.
- Confirm the room type is actually configured: a night for a room type not
  in `config/agent.yaml: room_types` is silently skipped by the sync step.

## An item is stuck at `sending`

A process died between claiming an item and finishing the publish.
`tools/run.py` calls `core.store.Store.reap_stuck_sending()` on every pass,
which moves anything stuck for more than 30 minutes to `failed` so you see
it in the queue instead of it vanishing. Use
`python3 tools/review.py retry <id>` once the cause is fixed.

## A rate move gets approved but nothing publishes

In `mode: shadow`, this is expected — see `workflows/80-review.md` step 4.
`python3 tools/review.py send` prints `blocked <id> (approval kept): ...`
and the item goes straight back to `approved`, not `failed`; run `list` and
it is there waiting for `mode: live`.

In `mode: live`, check `python3 tools/review.py show <id>` — a real write
error lands the item in `failed` with the reason on its `error` field (a
room type your PMS adapter does not recognise, a closed adapter) rather
than silently dropping it. `data/exports/pms_writes.csv` shows what a
`csv`/`mock`-mode PMS would have received instead of a live write.

## The comp-set median always shows 1.0, or pace always shows 0

That is the honest default with no data, not a bug — see
`docs/how-it-works.md` "Design decisions" #4. Add
`data/imports/comp_rates.csv` / `data/imports/pace.csv` (or, for the demo,
`fixtures/inbound/comp_rates.json` / `pace.json`), then `make doctor` should
show the "signal sources" line reading from the real file.

## `python3 tools/*.py` says `ModuleNotFoundError: No module named 'core'`

You ran it with a Python that is not the repo's virtualenv, or from outside
the repo root. Use `make run` / `make doctor` / etc. (they call
`.venv/bin/python` for you), or run `.venv/bin/python tools/run.py` directly
from the repo root.

## Still stuck

`data/logs/*.jsonl` has every decision the agent made, in order, with a run
id. `python3 tools/review.py show <id>` has the full event trail for one
item. If neither explains it, that is a real bug — describe exactly what you
ran and what you expected, and ask.
