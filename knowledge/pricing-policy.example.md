# Pricing policy — Hotel Aurora

<!--
Copy this to knowledge/pricing-policy.md and replace everything with your
own numbers and reasoning. Unlike knowledge/property.md and faq.md, nothing
in this file is read by a prompt - it exists so a human (you, or the next
person who inherits this account) can see WHY config/agent.yaml's numbers
are what they are, in plain language, next to the config that encodes them.
Keep it current whenever you change a guardrail.
-->

## Our room types and why the floor/ceiling sit where they do

| Room type | Base rate | Floor | Ceiling | Reasoning |
|---|---|---|---|---|
| Classic Room | 140 | 95 | 220 | Floor covers housekeeping + fixed cost per stay; we have never sold below 100 even in January. Ceiling is roughly what the top comp-set member charges at their peak - we do not want to be the outlier that gets skipped. |
| Deluxe Room | 190 | 130 | 290 | Same logic, scaled to this room's own cost base. |
| Junior Suite | 260 | 180 | 400 | Only 6 rooms - a wider band because a single booking swings occupancy a lot. |
| Aurora Suite | 420 | 290 | 650 | Only 2 rooms. The ceiling is a genuine "would we actually charge this" number, not a formality - if you are hitting it often, raise it. |

Revisit this table every season. A floor that has not bound in three months
is probably set too low to matter; a ceiling you hit every event weekend is
probably too low.

## How we think about the season curve

`config/agent.yaml: season_multiplier` is one number per month. Ours is the
generic curve this template ships with - replace it once you have at least
a year of real occupancy data. The honest way to set it: take each month's
average occupancy over the last two years, normalise so the lowest month is
1.0, and use that ratio (capped somewhere sensible, like 1.4) as the
multiplier. Do not just copy a competitor's public rates - their cost base
and their room mix are not yours.

## What "aggressive" vs "conservative" autopilot actually means here

- **`autopilot: advise`** — everything waits for a person. Use this for your
  first two or three weeks, or any time you are testing a new guardrail
  value and want to see every proposal before anything can auto-publish.
- **`autopilot: guarded`** (the default we run on) — the engine publishes
  moves inside every guardrail on its own; anything unusual is still held.
  This is the setting the roster promise describes.
- **`autopilot: full`** — the same auto-eligible set as `guarded` in this
  template (the floor, ceiling and daily cap are structural, not a mode
  switch, and stay-rule changes are always held regardless of mode - see
  `docs/how-it-works.md` "Design decisions" #8). We do not currently see a
  reason to run `full` over `guarded` and have left it at `guarded`.

`max_move_pct`, `hold_cut_pct` and `comp_distance_pct` are the three numbers
worth revisiting most often. If the review queue is full of items you keep
approving without changing anything, the guardrails are probably tighter
than they need to be - loosen the relevant one a little and watch the next
week.

## Our stance on gap nights and deep cuts

We are comfortable discounting a genuinely empty night between two full
ones (`gap_cut_pct: 0.80`) - an empty room earns nothing. We are more
cautious about a broad slow-market cut (`deep_cut_pct: 0.75`, gated by
`deep_cut_pace_pts`/`deep_cut_occ_pct`) - a string of soft nights sometimes
means the market moved and sometimes means our own marketing has gone
quiet, and only one of those is fixed by cutting the rate. Check the
competitor scan in the thinking log before approving a run of deep cuts.

## Length-of-stay offers

We treat "Stay 3, pay 2" as a tool for filling a soft week without touching
the headline rate a guest sees on a search results page. `offer_occ_pct`
and `offer_pace_pts` decide when the engine proposes one; approving an
offer does not publish anything on its own (there is no reservation-system
write for a length-of-stay deal here) - it is a nudge for a person to set
the actual promotion up.
