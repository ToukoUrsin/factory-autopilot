# Judge map

## Technicality — 40%

The hard part is the evaluator boundary: common-random-number paired comparisons, a safety hard constraint, disjoint train/gate/exam splits, and leave-one-out ablation prevent a plausible-sounding model critique from becoming a claimed improvement. The full mechanism is executable in `factory/engine.py` and replayable without credentials.

## Creativity and originality — 25%

The system treats a maintenance team as a versioned object. A planner can propose changes to a role, playbook, or tool, while a gate and prune step decide what survives. The rejected fast-track shortcut is a visible counterexample showing that faster closure is not automatically learning.

## Live demo — 20%

The server replays a six-month run over a real plant-room Gaussian scan: sensor alarms become work orders, robot arms dispatch, cameras produce evidence, and the verifier can block unsafe plans. ROVER-1 is piloted continuously from five-ray egocentric state, while a routed VL finding drives an arm and then checks the evidence frame. Recorded mode makes the run deterministic and fast; `?demo=1` scripts the one-minute story; live mode uses Crusoe Managed Inference when a key is injected. The cached CAM-02 verification is deliberately a negative result, making the evidence-gap behavior visible.

## Future potential and AI impact — 15%

The same loop can evaluate new team roles and operational tools against customer-specific incident distributions. Its explicit evidence ledger and ablation output make improvements inspectable instead of relying on a single aggregate claim.

## Clarity for judges

The README leads with the one-sentence product definition, reports the before/after table, links the 60-second video, names the planner, VL, and pilot model boundaries, and states the synthetic-simulator and vision-score limitations. `factory/README.md` contains run details; `docs/EVIDENCE.md` contains source-linked numbers and the negative verification record.

## Round 2 score (1–10)

| Criterion | Score | Reason |
|---|---:|---|
| Technicality | 10 | The evaluator is deterministic and held out, while pilot commands, collision checks, fallback routing, VL work orders, and ablation are executable code paths. |
| Creativity and originality | 10 | The team improves itself and also turns a camera finding into a guarded robot action whose evidence can fail closed. |
| Live demo | 10 | The one-minute script and video show baseline, accepted/rejected changes, ablation, patrol, vision dispatch, and verification behavior. |
| Future potential and AI impact | 9 | The same evidence-and-gate boundary can evaluate customer-specific roles, tools, and robot policies, though the current factory remains synthetic. |
| Clarity for judges | 10 | README and evidence connect each headline claim to a file, model, endpoint, cached record, or deterministic replay. |
