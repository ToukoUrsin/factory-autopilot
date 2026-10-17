"""Camera object detection with Nemotron 3.5 Super VL on Crusoe.

The browser renders each camera's view of the scanned plant room (Gaussian splat
plus the placed robots and actuators) and posts the frame here. The vision agent
returns equipment boxes and any visible issue; issues are routed to the planner.
Results are cached per camera in evidence/vision/ so a reload replays them
without new model calls.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from .crusoe import available, chat

VL_MODEL = "nvidia/NVIDIA-Nemotron-3.5-Super-VL"
CACHE = Path(__file__).parent / "evidence" / "vision"
SAFE_ID = re.compile(r"^[A-Za-z0-9-]{1,16}$")

SYSTEM = (
    "You are the vision agent of a factory maintenance team. You look at one CCTV frame of a plant room "
    "(pumps, pipework, valves, gauges, electrical panels). Orange robot arms on black bases, red handwheels, white sensor beacons and thin light beams are the team's own overlay equipment: ignore them. "
    "Detect up to 6 notable objects and report anything a maintenance planner should act on (leaks, corrosion, "
    "obstructions, missing guards, trip hazards, open panels, unreadable gauges). Do not invent issues; if none are visible say so. "
    "Reply with JSON only."
)
SCHEMA = {
    "type": "object",
    "properties": {
        "detections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "box": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
                    "issue": {"type": "string"},
                    "severity": {"type": "string", "enum": ["none", "info", "warn", "crit"]},
                },
                "required": ["label", "box", "severity"],
            },
        },
        "summary": {"type": "string"},
        "route_to_planner": {"type": "boolean"},
    },
    "required": ["detections", "summary", "route_to_planner"],
}


def cached(camera: str) -> dict[str, Any] | None:
    path = CACHE / f"{camera}.json"
    return json.loads(path.read_text()) if SAFE_ID.match(camera) and path.is_file() else None


def all_cached() -> dict[str, Any]:
    return {p.stem: json.loads(p.read_text()) for p in sorted(CACHE.glob("*.json"))} if CACHE.is_dir() else {}  # includes verify-<camera> evidence checks


def detect(camera: str, station: str, image_data_url: str) -> dict[str, Any]:
    if not SAFE_ID.match(camera) or not image_data_url.startswith("data:image/"):
        return {"ok": False, "error": "bad request"}
    if not available():
        hit = cached(camera)
        return hit or {"ok": False, "error": "CRUSOE_API_KEY not set and no cached result"}
    user = [
        {"type": "text", "text": f"Camera {camera} watching station {station}. Boxes as [x0, y0, x1, y1] in 0-1000 image coordinates."},
        {"type": "image_url", "image_url": {"url": image_data_url}},
    ]
    text, record = chat("vision", SYSTEM, user, model=VL_MODEL, max_tokens=4000, thinking="off", json_schema=SCHEMA, attempts=4, deadline_s=90)
    result: dict[str, Any] = {"ok": False, "camera": camera, "station": station, "call": record, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    if text:
        try:
            value = json.loads(text[text.index("{") : text.rindex("}") + 1])
        except ValueError:
            value = None
        if isinstance(value, dict) and isinstance(value.get("detections"), list):
            dets = []
            for d in value["detections"][:8]:
                box = d.get("box") if isinstance(d, dict) else None
                if not (isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box)):
                    continue
                dets.append({"label": str(d.get("label", ""))[:40], "box": [max(0, min(1000, float(v))) for v in box],
                             "issue": (str(d.get("issue") or "")[:120] or None), "severity": d.get("severity") if d.get("severity") in ("none", "info", "warn", "crit") else "info"})
            result.update(ok=True, detections=dets, summary=str(value.get("summary", ""))[:240], route_to_planner=bool(value.get("route_to_planner")))
        else:
            record.update(ok=False, error="reply was not JSON")
    if result["ok"]:
        CACHE.mkdir(parents=True, exist_ok=True)
        (CACHE / f"{camera}.json").write_text(json.dumps(result, indent=1))
    return result


VERIFY_SYSTEM = (
    "You are the evidence verifier of a factory maintenance team. You get one CCTV frame taken after a work order. "
    "The team's robot is an orange articulated arm on a black wheeled base. Answer only what the frame shows. Reply with JSON only."
)
VERIFY_SCHEMA = {
    "type": "object",
    "properties": {"arm_on_site": {"type": "boolean"}, "note": {"type": "string"}},
    "required": ["arm_on_site", "note"],
}


def verify(camera: str, station: str, finding: str, image_data_url: str) -> dict[str, Any]:
    """Evidence check after a vision-triggered work order: is the arm on site at the finding?"""
    key = f"verify-{camera}"
    if not SAFE_ID.match(camera) or not image_data_url.startswith("data:image/"):
        return {"ok": False, "error": "bad request"}
    if not available():
        return cached(key) or {"ok": False, "error": "CRUSOE_API_KEY not set and no cached result"}
    user = [
        {"type": "text", "text": f"Work order at station {station}: {finding[:160]}. Is the orange robot arm on site, next to that equipment? One-sentence note."},
        {"type": "image_url", "image_url": {"url": image_data_url}},
    ]
    text, record = chat("verify", VERIFY_SYSTEM, user, model=VL_MODEL, max_tokens=1500, thinking="off", json_schema=VERIFY_SCHEMA, attempts=4, deadline_s=60)
    result: dict[str, Any] = {"ok": False, "camera": camera, "station": station, "finding": finding, "call": record}
    try:
        value = json.loads(text[text.index("{") : text.rindex("}") + 1]) if text else None
    except ValueError:
        value = None
    if isinstance(value, dict) and "arm_on_site" in value:
        result.update(ok=True, arm_on_site=bool(value["arm_on_site"]), note=str(value.get("note", ""))[:200])
        CACHE.mkdir(parents=True, exist_ok=True)
        (CACHE / f"{key}.json").write_text(json.dumps(result, indent=1))
    return result
