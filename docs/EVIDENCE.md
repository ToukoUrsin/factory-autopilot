# Evidence ledger

All figures below come from `factory/evidence/crusoe_live_20261017T212147Z.json` and were checked against a local replay of `factory.engine.aggregate`.

## Held-out exam

The exam uses 12 seeds (`201–212`) and is scored once, after selection.

| Metric | Before | After |
|---|---:|---:|
| Mean score | 66.5 | 92.8 |
| Unsafe actions | 21 | 0 |
| Blocked actions | 0 | 21 |
| Overdue | 25 | 0 |
| Evidence gaps | 46 | 15 |
| Stockouts | 18 | 8 |

Per-seed scores before were `63.9, 48.2, 73.2, 66.8, 74.3, 68.9, 72.5, 61.4, 82.5, 58.6, 66.8, 61.4`; after they were `92.6, 90.9, 93.0, 88.4, 94.4, 95.1, 93.0, 96.2, 96.2, 90.9, 90.5, 92.3`.

## Gate and ablation

| Edit | Kind | Gate result | Ablation signal |
|---|---|---|---|
| `verifier_role` | team | +11.5, 11 wins/0 losses, unsafe 15→0 | Removing it loses 16.4 exam points and restores 21 unsafe actions |
| `deadline_sweep` | playbook | +12.0, 12/0, unsafe 0→0 | Removing it loses 10.3 exam points and restores 25 overdue items |
| `reorder_buffer` | tool | +1.0, 9/3, unsafe 0→0 | Removing it loses 0.8 exam points and restores 18 stockouts |
| `fast_track` | playbook | **rejected**: −9.2, 1/11, unsafe 0→15 | Deliberately unsafe shortcut; never kept |

The accepted policy is generation 3: deadline sweep enabled, three-day reorder buffer, verifier role enabled, fast-track disabled.

## Crusoe and vision records

The live evidence contains 5 successful Crusoe calls, 6 recorded camera calls, and 1 routed camera finding. Planner and reviewer calls use `nvidia/NVIDIA-Nemotron-3-Super-120B-A12B`; camera calls use `nvidia/NVIDIA-Nemotron-3.5-Super-VL`. The deterministic evaluator makes no model calls.

The six camera records are `factory/evidence/vision/CAM-01.json` through `CAM-06.json`. CAM-02 is the routed finding at station S02: the VL output reports a grey cable resting across a red machine and sets `route_to_planner: true`. The follow-up `factory/evidence/vision/verify-CAM-02.json` uses the same VL family and returns `arm_on_site: false` because the orange arm is not visible in the frame. This is an inspectable evidence gap, not a successful closure claim. The browser uses the cached record unless `fresh` is requested.

The robot pilot is implemented in `factory/pilot.py`: it sends bounded egocentric state to `nvidia/Nemotron-3.5-Lightning-30B-A3B`, accepts only the five discrete commands in `COMMANDS`, and limits a response to five commands. Browser-side collision checks reject a move that cannot clear the scanned walk grid; the browser falls back to the A* planner when `/api/pilot` is unavailable. No pilot score or physical-world safety claim is included in the exam ledger.

`/api/demo` adds a deterministic leave-one-out `ablation` list at server startup. The three kept edits have score drops of 16.4, 10.3, and 0.8 points when removed; the rejected `fast_track` row records a -9.2 gate delta and 15 unsafe actions when enabled. These values are recomputed from `factory.engine.aggregate` over the 12 exam seeds; they are not model-generated metrics.

## Limits

The twin is synthetic, so the score is evidence that this evaluator selects the modeled mechanisms correctly, not a physical safety or savings guarantee. The six vision outputs are inspectable model evidence, but vision findings do not feed the simulator’s exam score. The pilot, work-order, and verification path is an interactive demonstration layer; the checked-in verification result is negative and does not establish that a physical arm reached a site.
