# Guardrails and safety

This agent talks to your guests and touches your systems. Everything below is
built in, not optional, and this page explains what it does and what is left for
you to decide.

## The two modes

| Mode | What happens |
|---|---|
| `shadow` (default) | The agent reads, thinks, drafts and queues. It **never** sends a message and **never** writes to your PMS. |
| `live` | Items you approved are really sent. Everything else still waits. |

`mode` lives in `config/hotel.yaml`. It is a global kill switch: flipping it back
to `shadow` stops every outbound action immediately, mid-schedule, with no other
change. `config/agent.yaml` can be stricter than `hotel.yaml`, never looser.

Two more brakes:

- `make run ARGS="--dry-run"` computes everything and writes nothing, even in
  live mode. Use it when you change a prompt.
- `review.require_approval_for` in `config/hotel.yaml` lists the actions that
  need a human even in live mode. The defaults are `send_email`, `send_message`,
  `pms_write`, `payment`, `publish`. Shortening that list is how you hand the
  agent more rope, one action at a time.

Every outbound action in the codebase goes through one function,
`core/review.py:assert_write_allowed`. There is no second path.

## The review queue

Nothing reaches a guest without passing through the queue.

```bash
make review                       # what is waiting
python3 tools/review.py show <id>  # the full draft and how it got there
python3 tools/review.py approve <id>
python3 tools/review.py edit <id> --body-file my-version.txt
python3 tools/review.py reject <id> --reason "wrong tone"
```

An item moves `new -> classified -> drafted -> pending_review` and then waits.
Only `tools/review.py` can write `approved`, `edited` or `rejected`; only
`tools/run.py` can write `sent`. A crash between "about to send" and "sent" is
picked up on the next pass and shown to you as failed rather than silently
retried.

**Your edits teach it.** When you rewrite a draft, the before and after are
stored. Over time that is what makes the drafts sound like your hotel instead of
like a machine.

## Revenue Management AI's own guardrails

This agent never talks to a guest and sends no message — the guardrails
below are what "never sent" means when the thing being sent is a price, not
an email. All are enforced in `tools/pricing_engine.py`, not just described
here — turning a rule off in `config/agent.yaml` is the only way to change
one, and `full` autopilot does not bypass any of them.

- **Rate floor and ceiling, per room type.** A cut is never published below
  its floor; a rise is never published above its ceiling — in every
  `autopilot` mode, including `full`. A floor-clamped cut that no longer
  beats the guest's current rate is dropped entirely, never published at
  parity.
- **Max daily move: ±10% of the formula rate** (`max_move_pct`). A gap-night
  fill is the one documented exemption, and it still respects the floor and
  ceiling.
- **Non-compounding.** Every proposal is priced off the formula rate, never
  off an existing published rate — a re-run cannot compound its own moves.
- **Weather never sets a price.** `run_repricing()` only ever reads
  `category: event` rows; weather rows are read exclusively by the forecast.
  This is structural, not a rule you could switch on by accident.
- **Stay-rule (MLOS) changes are always held for a human** — in every
  `autopilot` mode, with no way to add `sheets_write` back to an
  auto-publish path for this one kind. See "Design decisions" #8 in
  `docs/how-it-works.md`.
- **The near-term manual window** (`near_manual_days`, default 3 nights) is
  always held — arrivals this close are the duty manager's call, not the
  machine's.
- **The comp-set distance guardrail** (`comp_distance_pct`, default ±15%)
  holds anything autopilot would otherwise publish outside that band of the
  comp-set median.
- **The model is never in the numbers.** `core.llm.complete()` is called
  exactly once per pass, after every price and stay-rule decision is
  already final, to write a short note about the run. If that call fails for
  any reason, the run has already succeeded — nothing about pricing waits on
  it.
- **Card numbers are irrelevant here, and this agent never sees one.** It has
  no guest inbox; `core/redact.py` still runs on anything ingested through
  `tools/ingest.py`, as a defensive default shared by the whole family.

## What the agent will not do

- Publish anything while `mode: shadow`, or publish an item a human has not
  approved when the action needs approval.
- Take a payment, issue a refund, or move money. Payment adapters are
  read-only by design and this agent never calls one.
- Invent a comp rate, an event, or a pace figure that was not ingested. A
  signal with no data behind it defaults to neutral (comp median 1.0, pace
  0) rather than a guess — see `docs/integrations.md`.
- Auto-publish a stay-rule change, whatever `autopilot` mode is set to.

## Data handling

**What leaves your machine.** With `llm.provider: anthropic` or `claude-code`,
the prompt goes to Anthropic. That prompt contains the guest message and the
relevant property facts. With `llm.provider: mock` or `interactive`, nothing
leaves the machine at all.

**What is stored, and where.** Everything lives in `data/` inside this folder:
`agent.db` (SQLite), `logs/*.jsonl`, `exports/`. `data/` is gitignored. There is
no cloud service behind this repo and no telemetry.

**Card numbers are redacted on the way in.** Every inbound message passes through
`core/redact.py` before it is stored, logged or put into a prompt. A payment card
number is replaced with `[CARD REDACTED ****1234]`, and labelled CVC and expiry
values in the same message go with it. Detection requires a real card prefix and
a valid Luhn checksum, so booking references and door codes survive. IBANs are
masked the same way. Nothing you can do in config turns this off.

**Retention.** `privacy.retention_days` (default 365) is how long processed items
stay in the database. Deleting `data/agent.db` deletes everything the agent knows.

## GDPR, in practice

This agent's own data is almost entirely commercial, not personal: rates,
occupancy, comp-set positions, stay-rule decisions. It does not read a guest
inbox and does not store guest names or contact details of its own. Two
places worth a note anyway:

- **`nights` and the review queue hold no guest identity**, only aggregate
  counts (rooms on the books) per date and room type.
- **If your PMS adapter's reads ever surface a guest name or email** (some
  `list_reservations()` responses carry more than this agent asks for), it
  is not written to `data/agent.db` — only `capacity`/`otb_rooms`/pace are
  extracted. If you write your own PMS adapter, keep that boundary.
- **You are the controller** for whatever this software does with your PMS
  data, same as any other repo in this family. If you use `llm.provider:
  anthropic` or `claude-code`, the one thing Anthropic ever sees is the
  finished pricing summary for the morning note — never a guest record.

This is a practical summary, not legal advice.

## No guest-facing text, so no AI-disclosure line

Unlike a guest-messaging agent, Revenue Management AI produces no text a
guest ever reads — a price is not a message. The EU AI Act Article 50
guest-disclosure pattern the rest of this family uses does not apply here.
The audience for this agent's output is your own revenue manager, in the
review queue and in `config/agent.yaml`'s guardrails.

## Subscription or API: an honest note

Two ways to pay for the reasoning:

**Your Claude Code subscription** (`llm.provider: claude-code` or `interactive`).
Flat monthly cost, no per-message billing. This is genuinely the cheapest way to
run a small hotel's agent.

The caveat, plainly: a personal Pro or Max subscription is intended for
interactive use, and Anthropic's usage policy and rate limits apply to automated
use of it. A handful of scheduled runs a day is a normal way to work. Pointing
a busy inbox at it around the clock is not, and you will hit rate limits at the
worst moment. Read the terms and decide for yourself.

**The Anthropic API** (`llm.provider: anthropic`). Pay per token, no ambiguity
about automated use, proper rate limits, and usage you can attribute. This is
the right answer for production volume. `make report` shows what you are
spending.

Start on the subscription while you are learning what the agent does. Move to the
API when it becomes part of how the hotel runs.

## If something goes wrong

1. `mode: shadow` in `config/hotel.yaml`, or `AGENT_MODE=shadow` in `.env`. Every
   outbound action stops on the next pass.
2. Remove the schedule (`crontab -e`, `launchctl unload`, or
   `systemctl disable --now <slug>.timer`).
3. `make doctor` to see what the agent thinks its state is.
4. `data/logs/*.jsonl` has every decision, with the run id, in order.
