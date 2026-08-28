# Sub-agents in this repo

Both fold into this repo, both are **off by default**, and both are fully
optional — the repricing loop (`workflows/10-repricing.md`) does its whole
job without either. No coach layer applies to this agent (see the brief):
there is no free-text draft for a human to edit, so there is nothing for a
weekly coach pass to learn from — a changed number is just a new approved
value.

## Demand Forecasting AI — "The Oracle"

**Does.** "Watches what nearby hotels are charging (the industry calls this
rate shopping), plus local events, weather, and search/pace data, and
forecasts demand — feeding the Revenue Management AI so pricing is
market-aware, not just rule-based. Every forecast shows its working: each
signal's contribution night by night, and the engine's own tracked error
rate."

**Won't.** "Doesn't set prices itself; it advises the Quant."

**Output.** "Sharper pricing on event/peak dates; 2–4% incremental RevPAR
over rules alone." ROI: `+3%` RevPAR on event & peak dates.

**In this repo.** `tools/forecast_engine.py`, toggled by
`config/agent.yaml: subagents.demand_forecasting.enabled`. Run it with
`python3 tools/run.py --once --forecast` (`workflows/21-demand-forecast.md`).
It reads the same `nights`/`comp_rates`/`events` signals as the repricing
engine and produces a 21-night occupancy projection plus a rate-shopping
panel with the same 2-consecutive-night, 10%-deviation callout discipline
the source system used. `weather_signal` is its own provable toggle — flip
it off and re-run to see the projection change by exactly the weather
front's points.

**The one honest gap.** The roster's promise is that the Oracle "feeds" the
Quant. In the source system this repo was built from, that was narrative
only — the forecast and the repricing engine never actually talked to each
other. This repo keeps that same independence rather than silently wiring
one into the other (see `docs/how-it-works.md` "Design decisions" #10 for
why: a real integration is a product decision about how much weight a
projection should carry against pace, and defaulting one in without you
choosing it would be worse than being honest that it does not exist yet).
Turning the Oracle on gets you a genuinely useful second opinion on where
demand is heading — it does not change tonight's rate.

## OTA Content & Parity AI — "The Cartographer"

**Does.** "Watches every OTA listing (Booking.com, Expedia, Airbnb…) around
the clock for the two things that quietly kill visibility: rate parity
breaks (a channel showing a cheaper price than your own site, which OTAs
punish with lower ranking) and content gaps (missing photos, thin
descriptions, wrong amenities, inconsistencies between channels). Flags
every break, drafts the fix, and resyncs the channel on approval."

**Won't.** "Doesn't set rates (the Quant's job) and doesn't create marketing
content (the Marketing & Social AI's job) — it distributes and polices what
they produce."

**Output.** "Higher listing conversion + zero parity violations." ROI: `+7%`
OTA listing conversion.

**In this repo.** `tools/parity_engine.py`, toggled by
`config/agent.yaml: subagents.ota_content_parity.enabled`. Run it with
`python3 tools/run.py --once --parity` (`workflows/22-ota-parity.md`). It
reads `data/imports/ota_rates.csv` / `ota_content_findings.csv` (or the
matching `fixtures/inbound/*.json` in mock mode — there is no channel-manager
adapter in this family, see `docs/integrations.md`), computes real parity
breaks (an OTA rate below your direct rate by more than
`parity_tolerance_pct`, not a status someone typed in), and scores every
channel's content health with the same blunt, explainable formula the
source used: every listing starts at 100, an open finding costs 22 points
(high severity) or 12 (medium), the floor is 40, and a drafted-but-unapplied
fix keeps costing points — only `applied` gives them back. `content_sync:
false` degrades gracefully to a rates-only sweep and says so
("Content diff skipped") rather than silently skipping.

**Every fix is a template, never a model call.** `tools/parity_engine.py`
imports neither `core.llm` nor `core.review` (a test in
`tests/test_parity_engine.py` checks this directly) — a photo-gap draft, a
description swap, an amenity-flag fix, a stale-promo removal are all pure
functions of the finding's own `detail` text. Nothing is invented; nothing
sounds different between two runs of the same finding.

**Publishing is honestly labelled as simulated.** There is no
channel-manager adapter to actually push a rate or a photo set to an OTA
extranet. `python3 tools/review.py send` on an approved fix still runs the
full write guard (shadow mode blocks it exactly like any other action) and
marks the item applied with `(simulated — no channel adapter connected)` —
apply it in the channel's own extranet, or ask your Claude session to write
a real adapter (`docs/integrations.md#implement-your-own`).

**The shared KPI.** Both sub-agents' output rolls up into `tools/report.py`
alongside the Quant's own numbers — see `docs/benefits.md`.
