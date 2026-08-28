# knowledge/

This folder is the agent's memory of your property. Most agents in this
family read these files before drafting anything a guest sees. **Revenue
Management AI is different: nothing here is read by a prompt.** This agent
produces no guest-facing text at all, so `property.md`/`faq.md`/
`signature.md` below are the generic scaffold templates, shipped for
consistency across the family, and are optional for this repo specifically.
`pricing-policy.md` is the one file that actually matters here — a plain-
language record of *why* `config/agent.yaml`'s numbers are what they are,
for the next person (including a future you) who has to change one.

## What to put here

| File | What it holds |
|---|---|
| `pricing-policy.md` | **This agent's own.** Floor/ceiling reasoning per room type, season-curve notes, what your autopilot setting means in practice. See `knowledge/pricing-policy.example.md`. |
| `property.md` | Generic scaffold template. Not read by this agent. |
| `faq.md` | Generic scaffold template. Not read by this agent. |
| `signature.md` | Generic scaffold template. Not read by this agent — Revenue Management AI sends no messages. |

Copy the `.example.md` file that actually matters here:

```bash
cp knowledge/pricing-policy.example.md knowledge/pricing-policy.md
```

`knowledge/*.md` is gitignored (the `.example.md` files are not), because your
property notes are yours.

## How to write `pricing-policy.md`

**Write it the way you would brief the next revenue manager.** Short
sentences, the real reasoning, no marketing language. Nobody but a human
ever reads this file.

**Say why, not just what.** `config/agent.yaml` already has the numbers;
this file is for the reasoning that does not fit in a YAML comment — why the
Aurora Suite's ceiling is 650 and not 700, why you run `guarded` and not
`full`.

**Keep it dated.** A floor/ceiling table with no date on it goes stale
silently. Note when you last reviewed each one.

## Keeping it current

Whenever you change a guardrail in `config/agent.yaml`, update
`pricing-policy.md` in the same sitting — the reasoning is worth as much as
the number. A good trigger: every time you approve a run of held items
without changing anything, ask whether the guardrail that held them is
tighter than it needs to be, and if you loosen it, write down why here.
