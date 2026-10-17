"""One live Version N+1 run on Crusoe, recorded as non-secret evidence.

    hsec exec --only CRUSOE_API_KEY -- python3 -m factory.live

Nemotron Super plans (orders the auditor's whitelisted edits) and reviews; the
deterministic engine scores everything. The evidence file keeps each call's
record (model, latency, tokens, attempts, error), the model's choices and the
evaluator's verdicts, never the key or request headers.

Because the evaluator is deterministic, feeding the recorded choices back into
learn() reproduces the run exactly. This script checks that before writing, and
the server uses it to show the recorded run when no key is present.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .crusoe import BASE_URL, available, model_id, nemotron_critique, nemotron_propose
from .engine import learn

EVIDENCE_DIR = Path(__file__).parent / "evidence"
SCHEMA = "factory-live-v2"
LIMITS = [
    "The factory is a synthetic simulator: each edit's effect is a modelled mechanism, so the gains show the loop selects correctly, not that these rates hold in a real plant.",
    "Nemotron only orders whitelisted edits and writes the review; the audit, gate, prune and exam are deterministic and make no model calls.",
    "The scene backdrop is the public poster image of SuperSplat scene 8be1404d (merged_room_v2_3x, azha007); no splat bytes are bundled until redistribution rights are confirmed.",
]
EXAM_KEYS = ("score", "per_seed", "unsafe_actions", "blocked", "overdue", "evidence_gaps", "stockouts")


def summarise(result: dict[str, Any], *, ts: Optional[str] = None) -> dict[str, Any]:
    """Non-secret evidence for one learn() run."""
    generations = []
    calls = []
    for n, generation in enumerate(result["generations"][1:], start=1):
        proposal = generation.get("proposal") or {}
        if proposal.get("call"):
            calls.append(proposal["call"])
        generations.append(
            {
                "step": n,
                "edit": generation["edit"]["key"],
                "kind": generation["edit"]["kind"],
                "proposer": generation["proposer"],
                "auditor_order": proposal.get("auditor_order"),
                "model_order": proposal.get("model_order"),
                "picked": proposal.get("picked"),
                "changed_pick": proposal.get("changed_pick"),
                "why": proposal.get("rationale"),
                "expected_effect": proposal.get("expected_effect"),
                "call": proposal.get("call"),
                "accepted": generation["accepted"],
                "reason": generation["reason"],
                "gate": generation.get("gate"),
            }
        )
    critique = result.get("critique") or {}
    if critique.get("call"):
        calls.append(critique["call"])
    return {
        "schema": SCHEMA,
        "ts": ts or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provider": "crusoe",
        "base_url": BASE_URL,
        "model": model_id(),
        "evaluator": result["evaluator"],
        "splits": result["splits"],
        "generations": generations,
        "kept_edits": result["kept_edits"],
        "accepted_policy": result["accepted_policy"],
        "exam_before": {key: result["heldout_before"][key] for key in EXAM_KEYS},
        "exam_after": {key: result["heldout_after"][key] for key in EXAM_KEYS},
        "critique": {"text": critique.get("text"), "call": critique.get("call")},
        "calls": {
            "total": len(calls),
            "ok": sum(1 for call in calls if call.get("ok")),
            "attempts": sum(call.get("attempts") or 0 for call in calls),
            "latency_s": round(sum(call.get("latency_s") or 0 for call in calls), 2),
            "prompt_tokens": sum(call.get("prompt_tokens") or 0 for call in calls),
            "completion_tokens": sum(call.get("completion_tokens") or 0 for call in calls),
            "errors": [call["error"] for call in calls if call.get("error")],
        },
        "limits": LIMITS,
    }


def recorded_agents(evidence: dict[str, Any]):
    """Proposer and critic that return the recorded model output, in order."""
    steps = iter([g for g in evidence["generations"] if g["proposer"] != "ablation"])
    mismatches: list[str] = []

    def proposer(context: dict[str, Any]) -> dict[str, Any]:
        step = next(steps, None)
        keys = [candidate["key"] for candidate in context["candidates"]]
        if step is None or step["auditor_order"] != keys:
            mismatches.append(f"candidates {keys} != recorded {None if step is None else step['auditor_order']}")
            return {"order": None}
        return {"order": step["model_order"], "why": step["why"], "expected_effect": step["expected_effect"], "call": step["call"]}

    def critic(_summary: dict[str, Any]) -> dict[str, Any]:
        return dict(evidence.get("critique") or {"text": None, "call": None})

    proposer.mismatches = mismatches  # type: ignore[attr-defined]
    return proposer, critic


def replay(evidence: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Re-run the deterministic loop on the recorded choices. Returns (result, differences)."""
    proposer, critic = recorded_agents(evidence)
    result = learn(proposer=proposer, critic=critic)
    again = summarise(result, ts=evidence["ts"])
    diffs = list(proposer.mismatches)  # type: ignore[attr-defined]
    for key in ("kept_edits", "accepted_policy", "exam_before", "exam_after"):
        if again[key] != evidence[key]:
            diffs.append(f"{key}: replay {again[key]} != recorded {evidence[key]}")
    verdicts = lambda ev: [(g["edit"], g["accepted"], g["reason"]) for g in ev["generations"]]
    if verdicts(again) != verdicts(evidence):
        diffs.append(f"gate verdicts: replay {verdicts(again)} != recorded {verdicts(evidence)}")
    return result, diffs


def latest_evidence(directory: Path = EVIDENCE_DIR) -> Optional[Path]:
    """Newest v2 evidence file that has at least one successful model call."""
    for path in sorted(directory.glob("crusoe_live_*.json"), reverse=True):
        try:
            evidence = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if evidence.get("schema") == SCHEMA and evidence.get("calls", {}).get("ok"):
            return path
    return None


def main() -> int:
    if not available():
        print("CRUSOE_API_KEY is not set; inject it with hsec (see module docstring).", file=sys.stderr)
        return 2
    result = learn(proposer=nemotron_propose, critic=nemotron_critique)
    evidence = summarise(result)
    _, diffs = replay(evidence)
    evidence["replay"] = {"reproduced": not diffs, "differences": diffs}
    text = json.dumps(evidence, indent=1, ensure_ascii=False)
    if os.environ["CRUSOE_API_KEY"] in text:  # belt and braces: records never hold the key
        print("refusing to write evidence: key material found in output", file=sys.stderr)
        return 1
    EVIDENCE_DIR.mkdir(exist_ok=True)
    stamp = evidence["ts"].replace("-", "").replace(":", "").replace("+0000", "Z")
    path = EVIDENCE_DIR / f"crusoe_live_{stamp}.json"
    path.write_text(text + "\n")
    calls = evidence["calls"]
    print(f"model {evidence['model']}: {calls['ok']}/{calls['total']} calls ok, {calls['attempts']} attempts, {calls['latency_s']} s, errors {calls['errors']}")
    for g in evidence["generations"]:
        print(f"  {g['step']}. {g['edit']:<20} {g['proposer']:<28} {g['reason']}")
    print(f"exam {evidence['exam_before']['score']} -> {evidence['exam_after']['score']}, unsafe {evidence['exam_before']['unsafe_actions']} -> {evidence['exam_after']['unsafe_actions']}; replay reproduced: {not diffs}")
    print(f"critique: {evidence['critique']['text']}")
    print(f"wrote {path.relative_to(Path.cwd()) if path.is_relative_to(Path.cwd()) else path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
