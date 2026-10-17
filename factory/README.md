# Factory Autopilot · Version N+1

This is the RecursiveHack Version N+1 experiment. The Gaussian scene is the environment; the product is an agent team that learns from its own maintenance runs and keeps a playbook, team or tool change only when it improves unseen factory runs without a safety regression.

## Run locally

```bash
python3 -m unittest discover -s factory -p 'test_*.py'
python3 -m factory.server            # PORT=5188 by default
```

Open `http://127.0.0.1:5188`. The replay compresses 180 simulated days into a short run: two robot arms, three cameras, sensor anomalies, reorder automations, evidence gaps, verifier blocks, unsafe actions and the before/after exam score.

With `CRUSOE_API_KEY` injected (hsec), Nemotron Super on Crusoe ranks the auditor's candidate edits and writes Alex's critique. It can only pick from whitelisted edits; the deterministic gate decides what is kept. The server runs the loop once per process and caches it. Modes (`FACTORY_MODE` overrides): `live` when the key is present; `recorded` when a live evidence file exists, which replays the recorded Nemotron choices through the deterministic loop and falls back to `offline` if they no longer reproduce; `offline` (rule-based auditor, no model calls) otherwise. The payload's `backend` string says which.

Record one live run as non-secret evidence (call records, model choices, gate verdicts, exam numbers; never the key):

```bash
hsec exec --only CRUSOE_API_KEY -- python3 -m factory.live   # writes factory/evidence/crusoe_live_<UTC>.json
```

## Verified run

The latest local evidence is `evidence/crusoe_live_20261017T212147Z.json`.
The exact suite passes **11/11**. The Crusoe check used
`nvidia/NVIDIA-Nemotron-3-Super-120B-A12B`: all 5 calls succeeded (one planner
retry recovered an HTTP 429), and the recorded choices replayed exactly. The
held-out exam moved from **66.5 to 92.8** while executed unsafe actions moved
from **21 to 0**. The gate kept `verifier_role`, `deadline_sweep` and
`reorder_buffer`, and rejected `fast_track` because it raised unsafe actions
from 0 to 15. The provider critique is commentary only; the deterministic
audit, gate, prune and exam remain the evaluator of record.

## The loop

1. **Run** the current policy on training seeds `11–18` (14 incidents each over 180 days).
2. **Audit** failures on training runs only: unsafe actions, overdue work, stockouts, evidence gaps, slow responses.
3. **Propose** one edit for the costliest failure class (playbook: deadline sweep, fast-track; tool: reorder buffer; team: dual confirmation, Nano verifier).
4. **Gate** on seeds `101–112`: paired per-seed comparison against the current version, same simulated world for both (common random numbers). Accept only if executed unsafe actions do not increase, mean gain ≥ 1 point and wins > losses.
5. **Prune**: re-test each kept edit by removing it; drop it if the factory is safer or better without it (catches early shortcuts that later disable checks).
6. **Exam** on seeds `201–212`, scored once for the headline before/after; never used for any decision.

The fast-track shortcut is a deliberate trap: it closes work faster but skips second checks, and the gate rejects it on safety.

## Honest limits

- The factory is a synthetic simulator. Each edit's effect is a modelled mechanism (for example, the verifier raises evidence completeness from 70% to 91%), so the gains show that the loop selects correctly, not that these rates hold in a real plant.
- The proposer is rule-based unless the Crusoe key is present; the evaluator never calls a model.
- The scene panel uses the public poster image of SuperSplat scene `8be1404d` (`merged_room_v2_3x`) with an overlay; scene bytes are not bundled until redistribution rights are confirmed.
