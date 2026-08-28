"""tools/parity_engine.py - OTA Content & Parity AI ("The Cartographer").

Pure functions, no I/O, **no LLM anywhere** - every draft below is a template
function, not a model call: same finding in, same words out, every time (see
docs/how-it-works.md "Design decisions" #9). This file never imports
`core.llm` or `core.review` on purpose.

Off by default (`config/agent.yaml: subagents.ota_content_parity.enabled`).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from tools.pricing_engine import Night, RoomTypeCfg, current_rate, formula_rate

SCORE_FLOOR = 40
SEVERITY_DEDUCTION = {"high": 22, "medium": 12}
PLACEMENT_LINE = 80
RANKING_NOTE = ("OTAs rank on content completeness as much as on price - listings "
               "under ~80 lose search placement.")

MASTER_DESCRIPTION = (
    "A calm, modern stay in the heart of the city. Every room has air "
    "conditioning, free wifi and a proper desk; suites add a separate sitting "
    "room. Breakfast runs 07:30 to 10:30, the bar from 17:00, and reception is "
    "staffed around the clock. Two minutes to the metro, five to the old town."
)


@dataclass
class ParityBreak:
    channel: str
    date: str
    room_type_id: str
    direct_rate: float
    observed_rate: float
    pct_under: float
    status: str = "violation"
    fix_draft: str = ""
    fix_status: str = "none"

    def unique_key(self, scan_date: str) -> str:
        return f"{scan_date}:{self.channel}:{self.date}:{self.room_type_id}"


@dataclass
class ContentFinding:
    channel: str
    kind: str  # photos | description | amenities | inconsistency
    detail: str
    severity: str = "medium"
    status: str = "open"  # open | drafted | applied
    fix_draft: str = ""

    def unique_key(self, scan_date: str) -> str:
        digest = hashlib.sha256(f"{self.channel}|{self.kind}|{self.detail}".encode()).hexdigest()[:10]
        return f"{scan_date}:{self.channel}:{self.kind}:{digest}"


@dataclass
class ChannelScore:
    channel: str
    score: int
    band: str  # healthy | watch | at-risk
    note: str
    open_count: int


@dataclass
class SweepResult:
    parity_breaks: list[ParityBreak]
    scores: list[ChannelScore]
    thinking_log: list[str]
    summary: dict[str, Any]


# --------------------------------------------------------------------------
# step 1 - rate parity: computed, not asserted (see docs/how-it-works.md #11)
# --------------------------------------------------------------------------
def compute_parity_breaks(nights: list[Night], room_types: dict[str, RoomTypeCfg],
                          ota_rates: list[dict], cfg: dict, tolerance_pct: float
                          ) -> list[ParityBreak]:
    by_cell = {(n.date, n.room_type_id): n for n in nights}
    breaks = []
    for row in ota_rates:
        night = by_cell.get((row["date"], row["room_type_id"]))
        rt = room_types.get(row["room_type_id"])
        if night is None or rt is None:
            continue
        formula = formula_rate(rt, row["date"], cfg)
        direct = current_rate(night, formula)
        observed = row["observed_rate"]
        if observed < direct * (1 - tolerance_pct):
            pct_under = round((1 - observed / direct) * 100, 1) if direct else 0.0
            breaks.append(ParityBreak(channel=row["channel"], date=row["date"],
                                      room_type_id=row["room_type_id"], direct_rate=direct,
                                      observed_rate=observed, pct_under=pct_under))
    return breaks


def draft_parity_fix(brk: ParityBreak) -> str:
    return (f"Rate-parity correction - {brk.room_type_id}, {brk.date}\n\n"
           f"{brk.channel} is showing {brk.observed_rate:g} against our direct rate of "
           f"{brk.direct_rate:g} for {brk.date} - {brk.pct_under:g}% under. Action: "
           f"re-push the rate plan and availability for {brk.date}, then re-check the "
           f"public listing against the direct site before closing the ticket. "
           f"{brk.channel}'s own parity monitor typically clears the flag on its next "
           f"crawl.")


# --------------------------------------------------------------------------
# step 2 - content scoring
# --------------------------------------------------------------------------
def content_score(findings: list[ContentFinding], channels: list[str]) -> list[ChannelScore]:
    """Every listing starts at 100; an open finding costs points by severity; the
    floor is 40 - a listing is never worth literally nothing. A drafted-but-unapplied
    fix still costs points; only ``applied`` returns them."""
    out = []
    for channel in channels:
        open_findings = [f for f in findings if f.channel == channel and f.status != "applied"]
        deduction = sum(SEVERITY_DEDUCTION.get(f.severity, 12) for f in open_findings)
        score = max(SCORE_FLOOR, 100 - deduction)
        if score >= 90:
            band, note = "healthy", "Complete against the master set - nothing here is holding the listing back in search."
        elif score >= 75:
            band = "watch"
            note = ("Still above the ~80 placement line, but the gaps compound - each "
                   "one costs a little reach." if score >= PLACEMENT_LINE else
                   "Under the ~80 line where OTAs start pushing a listing down the results page.")
        else:
            band, note = "at-risk", ("Well under the ~80 line. OTAs rank on content "
                                    "completeness as well as price, and a buried listing "
                                    "does not convert.")
        out.append(ChannelScore(channel=channel, score=score, band=band, note=note,
                                open_count=len(open_findings)))
    return out


def draft_content_fix(finding: ContentFinding) -> str:
    channel = finding.channel
    if finding.kind == "photos":
        return (f"Photo set sync - {channel}\n\n{finding.detail}\n\nAction: push the "
               f"missing frames from the master photo set through {channel}'s content "
               f"API, then reorder so the strongest shot sits first in slot 1 - the "
               f"thumbnail decides the click.\n\n{channel} re-indexes a changed photo "
               f"set within 24 hours.")
    if finding.kind == "description":
        return (f"Description refresh - {channel}\n\n{finding.detail}\n\nAction: replace "
               f"the live description with the master copy below, which carries the "
               f"terms the ranking model reads as amenities - without a word of "
               f"marketing filler.\n\n\"{MASTER_DESCRIPTION}\"")
    if finding.kind == "amenities":
        return (f"Amenity map fix - {channel}\n\n{finding.detail}\n\nAction: flip the "
               f"listed amenity flags to match the property record. These are filter "
               f"fields, not free text: a guest who filters on one of them cannot see "
               f"us at all today, whatever we are charging.")
    if finding.kind == "inconsistency":
        return (f"Take down stale copy - {channel}\n\n{finding.detail}\n\nAction: expire "
               f"the outdated promotion or claim in the {channel} extranet and remove "
               f"any reference to it from the description. Promotional copy is often "
               f"syndicated - re-check the other channels for the same line before "
               f"closing the ticket.")
    return (f"Listing fix - {channel}\n\n{finding.detail}\n\nAction: re-check the listing "
           f"against the master set and correct the field it disagrees on.")


# --------------------------------------------------------------------------
# the sweep
# --------------------------------------------------------------------------
def run_channel_sweep(nights: list[Night], room_types: dict[str, RoomTypeCfg],
                      ota_rates: list[dict], findings: list[ContentFinding],
                      channels: list[str], rules: dict, cfg: dict) -> SweepResult:
    log = ["Run channel sweep."]
    tolerance = cfg.get("parity_tolerance_pct", 0.01)
    breaks = compute_parity_breaks(nights, room_types, ota_rates, cfg, tolerance)
    if breaks:
        names = ", ".join(sorted({b.channel for b in breaks}))
        log.append(f"Checked the live rate on every channel - {len(breaks)} parity "
                  f"break(s): {names}.")
    else:
        log.append("Checked the live rate on every channel - no channel is undercutting "
                  "direct.")

    if rules.get("content_sync", True):
        open_findings = [f for f in findings if f.status != "applied"]
        log.append(f"Compared photos, description and amenity flags across {len(channels)} "
                  f"channel(s) - {len(open_findings)} gap(s) against the master set.")
        scores = content_score(findings, channels)
    else:
        log.append("Content diff skipped - listing-content syncing is switched off in "
                  "the rules, so this sweep checked rates only.")
        scores = [ChannelScore(channel=c, score=100, band="healthy",
                               note="Content sync is off - rates only.", open_count=0)
                 for c in channels]

    worst = min(scores, key=lambda s: s.score) if scores else None
    log.append(f"Scored each channel - " + ", ".join(f"{s.channel} {s.score}" for s in scores)
              + (f". {worst.channel} is the weakest at {worst.score}." if worst
                 and worst.score < PLACEMENT_LINE else "."))

    summary = {
        "channels_checked": len(channels), "parity_breaks": len(breaks),
        "content_gaps": sum(s.open_count for s in scores),
        "worst_channel": worst.channel if worst else None,
        "worst_score": worst.score if worst else None,
    }
    return SweepResult(parity_breaks=breaks, scores=scores, thinking_log=log, summary=summary)
