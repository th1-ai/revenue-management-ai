# Workflow: first-run setup

Objective: get Revenue Management AI from a fresh clone to a working demo,
then to real config, in one sitting.

## Steps

1. **Install and check.**
   ```bash
   make setup
   make doctor
   ```
   `make setup` creates the virtualenv, installs `requirements.txt`, and
   copies `.env.example` -> `.env` and every `config/*.example.yaml` ->
   `config/*.yaml` (only if those files do not exist yet - it never overwrites
   your own copies). `make doctor` will show a `FAIL` on "hotel identity"
   right after setup - that is expected, it means the property name is still
   the shipped placeholder "Hotel Aurora." Everything else should be `ok` or
   `warn`.

2. **Run the demo.** No credentials needed.
   ```bash
   make demo
   ```
   Expect to see the repricing engine reason through 21 nights and 4 room
   types, then the Demand Forecasting AI and OTA Content & Parity AI passes
   (force-enabled for this walkthrough only), then the line
   `DEMO OK — 75 items processed, 75 drafted, 0 sent (shadow)`. If you do not
   see that, stop and read `workflows/99-troubleshooting.md` before going
   further.

3. **Fill in the property.** Edit `config/hotel.yaml` (name, address,
   currency, languages). Then `config/agent.yaml` — this is the important
   one for this agent: your real room types under `room_types`, each with its
   own `base_rate`, `floor` and `ceiling`, plus `season_multiplier` if your
   demand curve does not look like the shipped generic one. See the comments
   in `config/agent.example.yaml`.

   **If `engine: simple`** (see README "Simple mode"), also update
   `simple.reference_room_type` to one of the ids you just put under
   `room_types` — the shipped value is `classic`, which will not exist once
   you replace the sample room types. `make doctor`'s "simple engine" line
   checks this and names the valid options if it is stale; `make run` fails
   the same readable way rather than crashing.
   ```bash
   cp knowledge/pricing-policy.example.md knowledge/pricing-policy.md
   ```
   Replace the placeholder numbers with your own pricing strategy notes — see
   `knowledge/README.md`.

4. **Point the engine at your live book.** `systems.pms.adapter` in
   `config/hotel.yaml` starts as `mock` (bundled fixtures only). Set it to
   `csv` (drop exports in `data/imports/`) or `cloudbeds` (a live API) — see
   `docs/integrations.md`. Run `make doctor` after changing it.

5. **Feed the signals no PMS exposes.** Pickup pace, competitor rates, local
   events/weather, and (for the Cartographer) OTA-observed rates and content
   findings are not a PMS field — they are your own CSV exports in
   `data/imports/` (`pace.csv`, `comp_rates.csv`, `events.csv`, `ota_rates.csv`,
   `ota_content_findings.csv`). `make doctor` shows which signal is reading
   from a real file and which is defaulting to neutral/empty. Full column
   list: `docs/integrations.md`.

6. **Pick how the agent thinks.** `config/hotel.yaml`'s `llm.provider` starts
   as `interactive` — it asks you, in this Claude Code session, instead of
   calling a model. That costs nothing extra, and the model is only ever
   used to write a short morning note about a run that already happened —
   see `docs/safety.md` for why that is safe by construction.

7. **Set your guardrails.** `config/agent.yaml`: `autopilot` (`advise` /
   `guarded` / `full` — start on `guarded`), `max_move_pct`, `near_manual_days`,
   `hold_cut_pct`, `comp_distance_pct`. These are the numbers the roster's
   "inside guardrails you set" promise refers to — read `docs/how-it-works.md`
   before changing any of them.

8. **Decide on the two sub-agents.** `config/agent.yaml`'s `subagents` block:
   both `demand_forecasting` and `ota_content_parity` start **off** — the
   repricing loop is fully useful without either. See
   `workflows/21-demand-forecast.md` and `workflows/22-ota-parity.md`.

9. **Re-check.**
   ```bash
   make doctor
   ```
   Once the property name is real and `room_types` has your own numbers, the
   "hotel identity" and "room types" lines turn green. Move on to
   `workflows/10-repricing.md` to run the loop for real.
