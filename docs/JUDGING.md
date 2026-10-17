# Judge map

## Technicality — 40%

The hard part is the evaluator boundary: common-random-number paired comparisons, a safety hard constraint, disjoint train/gate/exam splits, and leave-one-out ablation prevent a plausible-sounding model critique from becoming a claimed improvement. The full mechanism is executable in `factory/engine.py` and replayable without credentials.

## Creativity and originality — 25%

The system treats a maintenance team as a versioned object. A planner can propose changes to a role, playbook, or tool, while a gate and prune step decide what survives. The rejected fast-track shortcut is a visible counterexample showing that faster closure is not automatically learning.

## Live demo — 20%

The server replays a six-month run over a real plant-room Gaussian scan: sensor alarms become work orders, robot arms dispatch, cameras produce evidence, and the verifier can block unsafe plans. Recorded mode makes the run deterministic and fast; live mode uses Crusoe Managed Inference when a key is injected.

## Future potential and AI impact — 15%

The same loop can evaluate new team roles and operational tools against customer-specific incident distributions. Its explicit evidence ledger and ablation output make improvements inspectable instead of relying on a single aggregate claim.

## Clarity for judges

The README leads with the one-sentence product definition, reports the before/after table, names the model boundary, and states the synthetic-simulator and vision-score limitations. `factory/README.md` contains run details; `docs/EVIDENCE.md` contains source-linked numbers.
