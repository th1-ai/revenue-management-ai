# Workflow: OTA Content & Parity AI - "The Cartographer"

Objective: turn on rate-parity checking and content-health scoring across
your OTA channels, and work its queue. Off by default — the repricing loop
in `workflows/10-repricing.md` is fully useful without this.

## Before you turn it on

The Cartographer **does not set rates** (that is the Quant's job) and
**does not write marketing copy** — every fix it drafts either re-syncs a
rate/availability, flips a boolean amenity flag, substitutes a pre-approved
master description, or removes stale copy. Nothing is invented. See
`docs/safety.md`.

## Steps

1. **Enable it.**
   ```yaml
   # config/agent.yaml
   subagents:
     ota_content_parity:
       enabled: true
       content_sync: true          # off = rate-parity checks only
       parity_tolerance_pct: 0.01  # an OTA rate this much below direct is a violation
       channels: ["Booking.com", "Expedia", "Google Hotel Ads", "Airbnb"]
   ```

2. **Feed it what it cannot see on its own.** There is no channel-manager
   adapter in this family (`docs/integrations.md`), so parity and content are
   computed from what you give it: `data/imports/ota_rates.csv` (channel,
   date, room type, the rate you observed live on that channel) and
   `data/imports/ota_content_findings.csv` (channel, kind, detail, severity —
   whatever a listing audit, a script, or your own eyes found). In mock mode
   the equivalent `fixtures/inbound/*.json` files stand in.

3. **Run it.**
   ```bash
   python3 tools/run.py --once --parity
   ```

4. **Show what is waiting.**
   ```bash
   make review
   python3 tools/review.py show <id>
   ```
   A parity break's draft quotes the observed rate and the direct rate; a
   content finding's draft quotes the finding's own detail text (the number
   of missing photos, the character count, the exact stale promo name) — no
   generic filler.

5. **Approve, then publish.**
   ```bash
   python3 tools/review.py approve <id>
   python3 tools/review.py send
   ```
   Because no channel-manager adapter exists, `send` still runs the write
   guard (shadow mode blocks it exactly like any other action) but the
   "publish" itself is `(simulated — no channel adapter connected)`. Apply
   the fix on the channel's own extranet, or ask your Claude session to
   write a real adapter — `docs/integrations.md#implement-your-own`.

6. **Watch the content-health score jump.** `python3 tools/review.py send`
   on an applied content finding returns its points to the channel's score —
   that visible jump is the moment the roster's "Higher listing conversion"
   promise is actually about.

## Toggle the rule proof

Set `content_sync: false` and re-run — the sweep checks rates only and says
so ("Content diff skipped") rather than silently skipping.

## Schedule

`config/agent.yaml: schedule.ota_parity` (default: every 4 hours). See
`scheduler/` for cron/launchd/systemd snippets.
