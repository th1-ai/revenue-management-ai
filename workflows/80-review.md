# Workflow: working the review queue

Objective: work through everything Revenue Management AI is holding for a
person — rate moves, stay-rule changes, and (if the sub-agents are on) OTA
parity breaks and content findings.

## Steps

1. **List what is waiting.**
   ```bash
   python3 tools/review.py list
   python3 tools/review.py list --kind rate_move
   python3 tools/review.py list --kind mlos_change
   python3 tools/review.py list --status needs_human
   ```

2. **Look at one.**
   ```bash
   python3 tools/review.py show <id>
   ```
   Read the reason and the contribution breakdown out loud to the user in
   plain language — "the comp set is up 12% tonight, so this is +4%; pace is
   +6 points with 88% already sold, so this is +5%; the daily cap trimmed 3
   points off the top" is a sentence a duty manager can act on. Do not paste
   the JSON at them.

3. **Decide.**
   ```bash
   python3 tools/review.py approve <id>
   python3 tools/review.py edit <id> --proposed 245           # rate/gap-night
   python3 tools/review.py edit <id> --proposed-mlos 2        # stay-rule
   python3 tools/review.py reject <id> --reason "why"
   ```
   An edit is recorded as `learnings` even though this repo has no coach —
   it is still useful history for `make report` and for you to spot a
   pattern by hand (e.g. every deep cut this week has been edited up 5%,
   which is worth raising with the hotel as a `deep_cut_pct` change).

4. **Publish.**
   ```bash
   python3 tools/review.py send
   ```
   This claims everything `approved`/`edited`, then, per item kind: a
   `rate_move` calls `pms.set_rate()`; an `mlos_change` appends a row to
   `data/exports/mlos_changes.csv` (or a live Google Sheet) for you to apply
   in your PMS's rate-plan screen; an `ota_parity`/`ota_content` item is
   marked applied with a note that no channel adapter exists yet. In
   `mode: shadow`, every one of these prints
   `blocked <id> (approval kept): blocked: pms_write - mode is shadow...`
   and the item goes straight back to `approved` — your decision is not
   lost, nothing is discarded, and the same item publishes the moment
   `mode: live` is set and you run `send` again. That is correct, not a
   failure; see `docs/safety.md`.

5. **Clear a stuck one.** Only a `send` that raises a real error — not a
   shadow-mode block — lands an item in `failed`, with the error recorded
   on the item:
   ```bash
   python3 tools/review.py show <id>
   python3 tools/review.py retry <id>
   ```
   The two are easy to tell apart: `failed` means something genuinely went
   wrong (a room type your PMS adapter does not recognise, a closed
   adapter, a real write error). A guard block never reaches `failed` — the
   item is simply back in `approved`, exactly where it was before you ran
   `send`, waiting for `mode: live`.

## What always needs a human

Regardless of `autopilot` mode: any stay-rule (MLOS) change, any cut deeper
than `hold_cut_pct` (default −8%), anything inside the near-term manual
window (`near_manual_days`, default 3 nights), and anything whose proposed
rate sits outside `comp_distance_pct` of the comp-set median. Full detail:
`docs/how-it-works.md` "Autopilot."

## Digest

`config/hotel.yaml: review.digest_hour` sets the local hour for a "waiting
for you" summary — wire it into `make report` or your own script; there is
no built-in email digest in this repo (see `docs/integrations.md` for why
email is not one of this agent's adapters).
