# Measuring the benefit

## The roster case

**Revenue Management AI ("The Quant"):** `+4%` RevPAR (revenue). "Continuous
rate and stay-rule updates across all future nights, published to the PMS
inside your guardrails — recovering the 2–6% of RevPAR typically lost to
under-priced peak nights, over-priced slow nights, and empty gap nights."

**Demand Forecasting AI ("The Oracle"), if enabled:** `+3%` RevPAR on event
and peak dates. "Sharper pricing on event/peak dates; 2–4% incremental
RevPAR over rules alone."

**OTA Content & Parity AI ("The Cartographer"), if enabled:** `+7%` OTA
listing conversion. "Higher listing conversion + zero parity violations."

These are the roster's own figures, not this repo's promise on top of them —
see `docs/how-it-works.md` for exactly how each number is computed and where
this template is more conservative than the source it was built from (the
projected-uplift model deliberately credits only half of an up-move and a
fraction of a down-move's unlocked demand).

## What `make report` shows

```bash
make report
python3 tools/report.py --json
```

- **Total proposals seen** and **by kind** (`rate_move`, `mlos_change`,
  `offer`, and, if the sub-agents are on, `ota_parity`/`ota_content`) —
  volumes, from `data/agent.db`, nothing phoned home.
- **Waiting for a person** — the queue depth right now.
- **Published automatically** and the percentage — how much of the workload
  autopilot is genuinely carrying versus what a human still decides.
- **Rejected** — proposals a human discarded outright.
- **Rate changed after a human edit** — every `tools/review.py edit` is
  recorded in `learnings`; a pattern here (the same guardrail edited the
  same way repeatedly) is a signal to change `config/agent.yaml`, not to
  keep overriding by hand.
- **LLM calls and spend** — narration only. The two prompts in this repo
  never move a number, so this line should stay small even at volume; a
  spend spike here almost always means the LLM provider is being called for
  something outside this repo's own two tasks.

## Reading the "published automatically" number honestly

While `mode: shadow` (the default, and where every fresh clone starts),
nothing actually publishes — an auto-eligible proposal is recorded as
`auto_sent` for bookkeeping, but the write itself was blocked by the guard.
`make report`'s "published automatically" percentage in shadow mode tells
you what autopilot **would** have done, not what it did. Do not quote it to
anyone as a live result until `workflows/90-go-live.md` has actually been
worked through — at that point the same number means what it says.

## Caveats worth keeping in mind

- **The projected-revenue figure in the thinking log is a model, not a
  measurement.** It assumes half of an up-move's demand and a fraction of a
  down-move's unlocked demand actually convert — deliberately conservative,
  but still a projection, not what the PMS later shows as booked revenue.
  Compare it against real RevPAR over a full season before trusting the
  number on its own.
- **The Oracle's forecast does not feed the pricer** in this template (see
  `docs/how-it-works.md` "Design decisions" #10), so its own accuracy is not
  something `run_repricing()`'s output depends on — it is a second opinion,
  measured on its own terms if you choose to track it.
- **The Cartographer's content score is a proxy for conversion, not a
  measurement of it.** Nothing in this repo tracks OTA bookings before and
  after a fix. If you want the real number, pull it from your channel
  manager or your OTA extranet's own analytics and compare the dates around
  a fix being applied.
- **A short review history is not a track record.** A few days of shadow
  mode tells you whether the guardrails make sense for this property; it
  does not tell you what a full season of autopilot would have earned. Go
  live deliberately, and keep watching `make report` after you do.
