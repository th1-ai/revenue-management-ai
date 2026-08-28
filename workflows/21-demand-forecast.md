# Workflow: Demand Forecasting AI - "The Oracle"

Objective: turn on the 21-night occupancy outlook and rate-shopping panel,
and run it. Off by default — the repricing loop in `workflows/10-repricing.md`
is fully useful without this.

## Before you turn it on

The Oracle **advises, it never sets a price** (roster `cant`). Its forecast
does not feed `tools/pricing_engine.py:run_repricing()` — see
`docs/how-it-works.md` "Design decisions" #10 for why this repo keeps them
independent rather than quietly wiring one into the other. Turning this on
gets you a second opinion on where demand is heading, not a change in how
tonight's rate is set.

## Steps

1. **Enable it.**
   ```yaml
   # config/agent.yaml
   subagents:
     demand_forecasting:
       enabled: true
       weather_signal: true   # rain/sunny fronts move the forecast only
   ```

2. **Feed it events and weather, if you have not already.** The forecast
   reads the same `data/imports/events.csv` (or `fixtures/inbound/events.json`
   in mock mode) as the repricing engine — `category: event` rows drive the
   event radar, `category: weather` rows drive the weather front. See
   `docs/integrations.md`.

3. **Run it.**
   ```bash
   python3 tools/run.py --once --forecast
   ```
   If it prints "Demand Forecasting AI is off," step 1 was not saved to
   `config/agent.yaml` (not the `.example.yaml`).

4. **Read the outlook.** The thinking log walks through pickup pace, the
   rate-shopping callouts (only ever raised for a deviation that holds for 2+
   consecutive nights, so single-night noise never gets reported), the event
   radar, the weather front, and one headline: projected occupancy against
   what is already on the books, the strongest and softest nights.

5. **Toggle `weather_signal` off and re-run** to see the forecast provably
   change — the tab's rule proof, carried over from the demo this repo was
   built from.

## What it does not do

- It writes nothing. `run_forecast()` has no proposal type and no write path
  — this is structural, not a rule you could accidentally turn off.
- It does not touch `pricing_days`/`nights` overrides, MLOS, or the review
  queue.
- Its own accuracy is not tracked yet — carry over the source's honest flag
  that a static "mean error" figure with no backtest behind it would be
  supreme confidence, not information. If you want one, back it out from
  `pricing_runs`-equivalent history in `data/agent.db` over time.

## Schedule

`config/agent.yaml: schedule.demand_forecast` (default: daily at 06:00). See
`scheduler/` for cron/launchd/systemd snippets, or
`python3 tools/schedule.py --command "tools/run.py --once --forecast" --cadence daily`.
