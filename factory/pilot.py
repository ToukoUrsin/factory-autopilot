"""Robot pilot: Nemotron drives a mobile robot with discrete commands.

The browser sends the robot's egocentric state (goal bearing and distance, free
distance along five rays from the scanned floor's walk grid). Nemotron 3.5
Lightning, the sub-agent model, answers with up to five commands from a fixed
vocabulary; the browser executes them and refuses any move into an obstacle.
"""

from __future__ import annotations

import json
from typing import Any

from .crusoe import available, chat

PILOT_MODEL = "nvidia/Nemotron-3.5-Lightning-30B-A3B"
COMMANDS = ["forward", "back", "left", "right", "stop"]
SYSTEM = (
    "You drive a wheeled factory robot on a scanned plant-room floor. Commands: "
    "forward (move 1.2 m ahead), back (move 1.2 m backwards), left (turn 45 degrees left), "
    "right (turn 45 degrees right), stop (you have arrived). "
    "Goal bearing is in degrees: negative = goal is to your left, positive = to your right, 0 = straight ahead. "
    "Rays give free distance in metres before an obstacle at -90 (left), -45, 0 (ahead), +45, +90 (right). "
    "Never drive forward if the ahead ray is under 1.4 m. Turn toward the goal, go around obstacles, stop within 1.5 m of the goal. "
    "Reply with JSON only: {\"commands\": [1 to 5 commands], \"reason\": short phrase}."
)
SCHEMA = {
    "type": "object",
    "properties": {
        "commands": {"type": "array", "items": {"type": "string", "enum": COMMANDS}, "minItems": 1, "maxItems": 5},
        "reason": {"type": "string"},
    },
    "required": ["commands", "reason"],
}


def pilot(state: dict[str, Any]) -> dict[str, Any]:
    if not available():
        return {"ok": False, "error": "CRUSOE_API_KEY not set"}
    try:
        view = {
            "robot": str(state["robot"])[:16],
            "goal": str(state.get("goal", ""))[:60],
            "goal_bearing_deg": round(float(state["bearing"])),
            "goal_distance_m": round(float(state["distance"]), 1),
            "rays_m": {k: round(float(v), 1) for k, v in dict(state["rays"]).items()},
            "last_commands": [c for c in state.get("last", []) if c in COMMANDS][-6:],
        }
    except (KeyError, TypeError, ValueError):
        return {"ok": False, "error": "bad state"}
    text, record = chat("pilot", SYSTEM, json.dumps(view), model=PILOT_MODEL, max_tokens=400, thinking="off", json_schema=SCHEMA, attempts=3, deadline_s=20)
    out: dict[str, Any] = {"ok": False, "call": record}
    try:
        value = json.loads(text[text.index("{") : text.rindex("}") + 1]) if text else None
    except ValueError:
        value = None
    if isinstance(value, dict):
        cmds = [c for c in value.get("commands", []) if c in COMMANDS][:5]
        if cmds:
            out.update(ok=True, commands=cmds, reason=str(value.get("reason", ""))[:80])
    return out
