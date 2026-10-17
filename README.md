# Factory Autopilot · Version N+1

An agent team that runs a real, scanned plant room and rewrites its own team, playbook and tools, keeping a change only when it measurably helps on **unseen** six-month runs.

Built during The Recursive Agents Hackathon (Crusoe × Cerebral Valley, 17 Oct 2026). Track: **Version N+1**. All agents run on **Crusoe Managed Inference** (Nemotron).

## What you see

- **The factory is a real Gaussian splat scan** (14.2M splats) of a mechanical plant room, rendered in the browser with `@playcanvas/supersplat-viewer`.
- **Robots, valve actuators, sensors and camera masts are 3D objects inside the scan**, placed on surveyed equipment. Mobile arms path-find across the scanned floor (A* on a walk grid) and reach the valves with inverse kinematics.
- **Six cameras** render their view of the scan; **Nemotron 3.5 Super VL** detects equipment and issues in each frame. A finding becomes a work order, an arm is dispatched, and a second VL call checks the evidence frame.
- **The learning loop** (below) decides which changes carry into the next generation; each kept change is then re-tested by removing it.

## Result (12 unseen exam runs)

| | Team N (baseline) | Team N+3 (learned) |
|---|---|---|
| Exam score | 66.5 | **92.8** |
| Unsafe robot actions executed | 21 | **0** |
| Evidence gaps | 46 | 15 |

What carries forward (exam points lost if the change is removed from N+3): verifier sub-agent −16.4 (21 unsafe actions without it), deadline sweep −10.3, reorder tool −0.8 (marginal). Rejected: fast-track (faster, 2.0 days, but 15 unsafe actions).

## Run

```bash
python3 -m unittest discover -s factory -p 'test_*.py'
CRUSOE_API_KEY=... python3 -m factory.server      # http://127.0.0.1:5188 ; ?demo=1 plays the 1-minute demo
```

Without a key the server replays recorded Nemotron calls from `factory/evidence/`.

See [`factory/README.md`](factory/README.md) for the loop, the gate and the evidence files.

## Credits and rights

- Scene: SuperSplat scene `8be1404d` (`merged_room_v2_3x`), streamed from the SuperSplat CDN at runtime; not bundled in this repo.
- Navigation grid and surveyed equipment positions (`factory/static/scene-map.js`) derived from Phylax, a teammate's project, with permission.
- `@playcanvas/supersplat-viewer` and `playcanvas` (MIT), vendored in `factory/static/vendor/`.
