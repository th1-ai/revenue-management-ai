---
name: revenue-management-ai
description: Run Revenue Management AI ("The Quant") — Runs your pricing the way the big-league revenue systems do — continuously, not once a day.. Use when the user asks to run the agent, check what is waiting for review, approve or reject a draft, or asks how the agent is doing. Trigger phrases: "run The Quant", "/revenue-management-ai", "check the queue", "what is waiting for me", "approve that draft".
---

# Revenue Management AI

Runs Revenue Management AI's repricing loop and works its review queue.
Everything happens from the repo root; every command below exists and
works.

## Before anything else

Read `README.md` if you have not this session, and `workflows/10-repricing.md`
for the main loop. If the user has never run this agent, start at
`workflows/00-setup.md` instead and walk them through it.

## The loop

**1. Check the agent is healthy.**

```bash
make doctor
```

Any `FAIL` line has a fix hint. Fix it before going further. `WARN` lines are
worth mentioning but do not stop the run — an empty `room_types` or a
placeholder hotel name is expected on a fresh clone.

**2. Run one pass.**

```bash
make run                             # sync + reprice + classify + publish/queue
make run ARGS="--dry-run"            # compute everything, write nothing
make run ARGS="--as-of 2026-09-01"   # rehearse against a specific date
```

If `llm.provider` is `interactive`, the pass finishes every pricing decision
first, then stops with exit code 3 while it waits for you to write the
morning note. Read `data/pending/*.prompt.md`, write your answer as JSON to
the matching `*.answer.json` following the schema exactly, then run the same
command again — nothing about pricing is recomputed. (That "3" is
`tools/run.py`'s own exit code — `make run` prints `make: *** [run] Error 3`
on screen but its own exit status is always 2, GNU Make's convention for
any failed recipe. Either way, it means the same thing: go answer the
prompt.)

**3. Show what is waiting.**

```bash
make review
python3 tools/review.py show <id>
```

Summarise it for the user in plain language: which night and room type, the
reason (pace, comp position, an event), the contribution breakdown, and how
far from a threshold it sits. Do not paste raw JSON at them.

**4. Act on their decision.**

```bash
python3 tools/review.py approve <id>
python3 tools/review.py edit <id> --proposed 245           # override a rate
python3 tools/review.py edit <id> --proposed-mlos 2        # override a stay rule
python3 tools/review.py reject <id> --reason "<why>"
python3 tools/review.py send                                # publish approved/edited
```

In `mode: shadow` (the default), `send` always reports "blocked... nothing
leaves in shadow mode" — that is correct, not a failure. Read the draft back
to the user before approving.

**5. The two sub-agents, only if the user needs them.**

```bash
python3 tools/run.py --once --forecast   # Demand Forecasting AI ("The Oracle")
python3 tools/run.py --once --parity     # OTA Content & Parity AI ("The Cartographer")
```

Both are off by default (`config/agent.yaml: subagents.*.enabled`) and print
"is off" if not enabled — turn them on first, see `workflows/21-demand-forecast.md`
and `workflows/22-ota-parity.md`.

**6. Report.**

```bash
make report
```

## Rules

- **Never publish in shadow mode**, and never work around a blocked write.
  The error message says what to do.
- **Going live is the hotel's decision.** Only raise it after
  `workflows/90-go-live.md` has been worked through, including
  `python3 tools/review.py stale` to clear the shadow-mode backlog first.
- **Stay-rule (MLOS) changes always wait for a human**, in every autopilot
  mode — never suggest a config change to auto-publish one; there is not
  one.
- **Confirm before anything irreversible** — a published rate, a stay-rule
  change — even when it is approved.
- **Never print or paste a credential.**
- If a run fails, read the whole error, fix the cause, re-run, and note what
  you learned in `workflows/99-troubleshooting.md`.
