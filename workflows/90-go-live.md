# Workflow: shadow to live

Objective: decide, together with the hotel, whether Revenue Management AI is
ready to publish auto-eligible rate moves on its own instead of only
drafting them — and make the change safely if so.

This is the hotel's decision, never the agent's. Do not suggest it until the
checklist below is genuinely true, and when you do raise it, say plainly
what changes: **approved and auto-eligible items start actually reaching
your PMS.**

## Checklist

- [ ] `make doctor` is clean (no `FAIL` lines). `warn` on `mode` and `llm
      provider` is expected until you flip them.
- [ ] `config/hotel.yaml` has the real property details, and
      `config/agent.yaml` has your real room types with a floor and a
      ceiling you actually stand behind — not Hotel Aurora's.
- [ ] `systems.pms.adapter` is a real one (`csv` or `cloudbeds`, not `mock`)
      and `make doctor` shows it healthy. Going live on `mock` would only
      ever touch the fixtures.
- [ ] At least a few days of real `make run` passes have gone through the
      review queue — not just the demo. Read `make report`: how many
      proposals, how many you edited, how many you rejected.
- [ ] You have looked at the held-for-review reasons for a week and they
      make sense — the guardrails (`config/agent.yaml`) match how this
      property actually wants to be run, not the shipped defaults blindly.
- [ ] You have decided which `autopilot` mode to start on. `guarded` is the
      honest middle ground the roster describes; `advise` is the cautious
      choice if you would rather approve every single move for a while
      longer even after going live.
- [ ] If either sub-agent is on, its inputs (`data/imports/*.csv`) are real,
      not the bundled fixtures.

## Making the change

1. Edit `config/hotel.yaml`:
   ```yaml
   mode: live
   ```
2. `review.require_approval_for` still lists `pms_write` and `sheets_write`
   by default — it should, for now. Going live only means an **approved**
   item, or an auto-eligible one once you also narrow
   `require_approval_for`, actually publishes; it does not change what needs
   approval on its own. There is no config that skips a fired guardrail.
3. **Clear the shadow backlog.** Everything sitting in `pending_review` from
   before today was computed against yesterday's book and is stale by the
   time you trust the drafts:
   ```bash
   python3 tools/review.py stale
   ```
   Re-run `make run` to get fresh proposals against the current live book.
4. Run `make doctor` again to confirm.
5. Watch one publish go through by hand before trusting the schedule:
   ```bash
   make run ARGS="--as-of $(date +%F)"
   python3 tools/review.py list
   python3 tools/review.py approve <id>
   python3 tools/review.py send
   ```
6. **The bigger step: letting guarded autopilot actually auto-publish.**
   Remove `pms_write` from `review.require_approval_for` in
   `config/hotel.yaml` only once you have watched several days of
   auto-eligible proposals and agree with every one of them. This is the
   one change that turns "guarded autopilot" from "advise with extra steps"
   into the roster's promise: automatic publishing inside your guardrails,
   with the unusual moves still held. Stay-rule (MLOS) changes are never
   auto-published, in any autopilot mode or approval list — that gate is
   structural (`docs/how-it-works.md` "Design decisions" #8).
7. Tell the hotel exactly what just changed, in plain language: from the
   next scheduled run, a proposal inside every guardrail publishes itself to
   the PMS; anything unusual still waits in the queue exactly as before.

## Going back to shadow

```yaml
mode: shadow
```
in `config/hotel.yaml`, or `AGENT_MODE=shadow` in `.env` for one run. Either
stops every publish — rate and stay-rule alike — on the next pass,
mid-schedule, with no other change required.
