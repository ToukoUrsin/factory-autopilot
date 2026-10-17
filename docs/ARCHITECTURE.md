# Architecture

Factory Autopilot separates model choice from measurement. The team may ask Nemotron to rank a small, explicit set of edits; only the deterministic simulator can accept an edit.

```mermaid
flowchart TB
  subgraph Inference[Crusoe Managed Inference]
    S[Nemotron Super planner]
    V[Nemotron 3.5 Super VL\nvision + evidence verifier]
    R[Nemotron Super reviewer]
  end
  subgraph Local[Local deterministic code]
    W[Whitelist in engine.py]
    D[Six-month digital twin]
    AU[Training audit]
    GA[Paired gate]
    AB[Leave-one-out ablation]
    EX[Held-out exam]
  end
  UI[Browser scan + robot overlays]
  UI --> V
  V --> UI
  D --> AU --> S --> W --> GA
  W --> D
  GA --> AB --> EX --> R
  EX --> UI
```

The seed splits are fixed and disjoint: training `11–18`, gate `101–112`, exam `201–212`. Each incident consumes the same random draws for parent and candidate policies. A gate rejects a candidate if unsafe actions increase, the mean score gain is below 1.0, or it does not win more paired seeds than it loses. A reviewer receives exam results after selection and writes commentary only.

The HTTP server has three safe modes: live (Crusoe key present), recorded (a checked-in live evidence file replays exactly), and offline (rule-based proposer). `/api/replay` runs a telemetry-rich deterministic six-month run for an exam seed; `/api/detect` and `/api/verify` validate browser frames and call the VL model or replay cached evidence.
