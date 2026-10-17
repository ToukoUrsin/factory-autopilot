"""Accelerated factory digital twin and the Version N+1 evaluator.

Everything in this module is deterministic and makes no model calls: the audit,
the gate, the ablation prune and the exam are pure functions of the policy and
the seed. Live Nemotron calls (factory/crusoe.py, factory/live.py) can only
reorder which whitelisted edit is tried next; they never score anything.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from random import Random
from statistics import mean
from typing import Any, Callable, Optional

HORIZON_DAYS = 180
TICKS_PER_DAY = 4  # telemetry every six simulated hours

# Layout traced over the public SuperSplat scan 8be1404d (merged_room_v2_3x).
# x/y are fractions of the room's floor plan; "streams" are the sensor channels
# mounted at the station.
STATIONS = [
    {"id": "S01", "name": "Pumps", "system": "mechanical", "x": 0.17, "y": 0.64, "streams": ["vibration", "inventory"]},
    {"id": "S02", "name": "Overhead services", "system": "mechanical", "x": 0.31, "y": 0.23, "streams": ["pressure"]},
    {"id": "S03", "name": "Rear wall", "system": "electrical", "x": 0.53, "y": 0.21, "streams": ["temperature", "vision"]},
    {"id": "S04", "name": "Control cabinet", "system": "electrical", "x": 0.76, "y": 0.29, "streams": ["temperature"]},
    {"id": "S05", "name": "Exit lighting", "system": "safety", "x": 0.80, "y": 0.69, "streams": ["vision"]},
    {"id": "S06", "name": "West end", "system": "fire", "x": 0.37, "y": 0.78, "streams": ["vision"]},
    {"id": "S07", "name": "East bay", "system": "mechanical", "x": 0.69, "y": 0.52, "streams": ["vibration", "pressure"]},
    {"id": "S08", "name": "Stores & evidence", "system": "documentation", "x": 0.53, "y": 0.53, "streams": ["inventory"]},
]
STATION_BY_ID = {station["id"]: station for station in STATIONS}

# Fixed cameras. Each evidence frame comes from the first camera covering the
# station; "vision" streams are those cameras' visual-check scores.
CAMERAS = [
    {"id": "CAM-N", "name": "North overhead", "x": 0.42, "y": 0.06, "covers": ["S02", "S03", "S04"]},
    {"id": "CAM-W", "name": "West mast", "x": 0.06, "y": 0.92, "covers": ["S01", "S06", "S08"]},
    {"id": "CAM-E", "name": "East corner", "x": 0.95, "y": 0.90, "covers": ["S05", "S07", "S04", "S08"]},
]

CHANNELS: dict[str, dict[str, Any]] = {
    "vibration": {"label": "vibration", "unit": "mm/s", "normal": 2.2, "alarm": 7.1, "noise": 0.18, "daily": 0.0},
    "pressure": {"label": "line pressure", "unit": "bar", "normal": 6.2, "alarm": 4.4, "noise": 0.05, "daily": 0.0},
    "temperature": {"label": "temperature", "unit": "°C", "normal": 34.0, "alarm": 55.0, "noise": 0.5, "daily": 1.6},
    "vision": {"label": "visual check", "unit": "%", "normal": 98.0, "alarm": 80.0, "noise": 0.6, "daily": 0.0},
    "inventory": {"label": "spares on hand", "unit": "pcs", "normal": 12.0, "alarm": 3.0, "noise": 0.0, "daily": 0.0},
}
WARN_LEVEL = 0.5  # fraction of the way from normal to alarm
RESET_LEVEL = 0.25
NOISE_CAP = 0.12  # noise never exceeds 12% of the normal→alarm span
STATION_BUSY_DAYS = 20  # one open excursion per sensor stream at a time

# Splits. Training runs are the only input to the failure audit and the proposer.
# Gate seeds decide acceptance. Exam seeds are scored once, at the end, for the
# reported before/after number, so selection on the gate cannot inflate it.
TRAIN_SEEDS = list(range(11, 19))
GATE_SEEDS = list(range(101, 113))
EXAM_SEEDS = list(range(201, 213))

MAINTENANCE_WINDOW_DAYS = 6
SUPPLIER_CUTOFF_DAYS = 6
UNSAFE_PROPOSAL_RATE = 0.22
MIN_GATE_GAIN = 1.0
FRAME_QUALITY_MIN = 0.30  # evidence frames at or below this are rejected
RECAPTURE_GAIN = 0.21  # a verifier-requested second angle from the arm's wrist camera


@dataclass(frozen=True)
class Policy:
    """Team structure and playbook knobs that Version N+1 may rewrite."""

    generation: int = 0
    name: str = "baseline"
    deadline_sweep: bool = False
    dual_confirmation: bool = False
    reorder_buffer_days: int = 0
    verifier_role: bool = False
    # Tempting shortcut: skip second checks on high-severity work to close faster.
    fast_track: bool = False


@dataclass(frozen=True)
class Incident:
    id: str
    day: int
    station_id: str
    system: str
    kind: str
    severity: str
    required_action: str
    required_evidence: str
    sensor: str


@dataclass
class Event:
    day: int
    incident_id: str
    station_id: str
    robot: str  # actor: SENSOR, ALEX, NANO, ARM-0x or a camera id
    kind: str
    status: str
    message: str
    t: float = 0.0  # fractional simulated day, for ordering and smooth replay
    data: Optional[dict] = None


@dataclass
class RunResult:
    seed: int
    policy: str
    generation: int
    heldout: bool
    events: list[Event]
    incidents: int
    resolved: int
    overdue: int
    unsafe_actions: int
    blocked: int
    evidence_gaps: int
    stockouts: int
    avg_response_days: float
    score: float
    slow: int = 0
    telemetry: Optional[dict] = None
    incident_list: list[Incident] = field(default_factory=list)

    def metrics(self) -> dict[str, Any]:
        return {
            "incidents": self.incidents,
            "resolved": self.resolved,
            "overdue": self.overdue,
            "unsafe_actions": self.unsafe_actions,
            "blocked": self.blocked,
            "evidence_gaps": self.evidence_gaps,
            "stockouts": self.stockouts,
            "avg_response_days": self.avg_response_days,
            "score": self.score,
        }

    def public(self) -> dict[str, Any]:
        out = {
            "seed": self.seed,
            "policy": self.policy,
            "generation": self.generation,
            "heldout": self.heldout,
            "events": [asdict(event) for event in self.events],
            "metrics": self.metrics(),
        }
        if self.telemetry is not None:
            out["telemetry"] = self.telemetry
            out["incidents"] = [asdict(incident) for incident in self.incident_list]
        return out


TEMPLATES = [
    ("vibration_drift", "high", "isolate and inspect pump", "vibration report", "vibration"),
    ("valve_leak", "high", "close valve and pressure-test", "pressure-test certificate", "pressure"),
    ("sensor_drift", "medium", "recalibrate sensor", "calibration certificate", "temperature"),
    ("camera_blind_spot", "medium", "move camera and capture frame", "inspection frame", "vision"),
    ("missing_spare", "medium", "order replacement before outage", "purchase order", "inventory"),
    ("egress_obstruction", "high", "clear route and photograph", "egress photo", "vision"),
    ("fire_equipment_check", "high", "inspect extinguisher and tag", "service tag", "vision"),
]
RISKY_KINDS = {"valve_leak", "vibration_drift", "egress_obstruction"}


def camera_for(station_id: str) -> dict[str, Any]:
    return next(camera for camera in CAMERAS if station_id in camera["covers"])


def make_incidents(seed: int, count: int = 14) -> list[Incident]:
    rng = Random(seed)
    kinds = [TEMPLATES[index % len(TEMPLATES)] for index in range(count)]
    rng.shuffle(kinds)  # unseen runs get a different incident mix order, not a replay
    spacing = (HORIZON_DAYS - 15) // max(1, count)
    last_use: dict[tuple[str, str], int] = {}
    incidents: list[Incident] = []
    for index, (kind, severity, action, evidence, sensor) in enumerate(kinds):
        station_draw = rng.randrange(len(STATIONS))
        day = 8 + index * spacing + rng.randrange(max(1, spacing - 4))
        # The fault lands on a station that carries its sensor channel, starting
        # from the drawn station and skipping streams with an open excursion.
        # The draws above are identical whatever station is chosen.
        pool = [station for station in STATIONS if sensor in station["streams"]]
        ring = [pool[(station_draw + offset) % len(pool)] for offset in range(len(pool))]
        free = [station for station in ring if day - last_use.get((station["id"], sensor), -999) >= STATION_BUSY_DAYS]
        station = free[0] if free else min(ring, key=lambda s: last_use.get((s["id"], sensor), -999))
        last_use[(station["id"], sensor)] = day
        incidents.append(
            Incident(
                id=f"INC-{seed}-{index + 1:02d}",
                day=day,
                station_id=station["id"],
                system=station["system"],
                kind=kind,
                severity=severity,
                required_action=action,
                required_evidence=evidence,
                sensor=sensor,
            )
        )
    return incidents


def _incident_points(*, overdue: bool, unsafe: bool, blocked: bool, gap: bool, stockout: bool, response: int, holding_days: int) -> float:
    """Per-incident score so one bad incident cannot floor a whole run at zero."""
    if unsafe:
        return 0.0
    value = 100.0
    value -= 40 if overdue else 0
    value -= 30 if gap else 0
    value -= 35 if stockout else 0
    value -= 5 if blocked else 0
    value -= max(0, response - 4) * 5
    value -= holding_days * 3
    return max(0.0, value)


def simulate(policy: Policy, seed: int, *, heldout: bool = False, count: int = 14, telemetry: bool = False) -> RunResult:
    incidents = make_incidents(seed, count)
    # Common random numbers: the world's draws depend only on the seed, and every
    # incident consumes the same draws in the same order whatever the policy. Two
    # policies on one seed therefore face the identical six months.
    rng = Random(seed * 7919 + 1)
    draws = [(rng.randrange(2, 8), rng.randrange(5, 12), rng.random(), rng.random()) for _ in incidents]

    events: list[Event] = []
    excursions: list[dict[str, Any]] = []
    overdue = unsafe = blocked = gaps = stockouts = resolved = 0
    response_days: list[int] = []
    points: list[float] = []
    robots = ["ARM-01", "ARM-02"]

    def emit(t: float, incident: Incident, actor: str, kind: str, status: str, message: str, **data: Any) -> None:
        events.append(Event(int(t), incident.id, incident.station_id, actor, kind, status, message, round(t, 3), data or None))

    for index, incident in enumerate(incidents):
        base_delay, order_lead, unsafe_draw, evidence_draw = draws[index]
        robot = robots[index % len(robots)]
        high = incident.severity == "high"
        risky = incident.kind in RISKY_KINDS
        skip_checks = policy.fast_track and high
        work_order = "WO-" + incident.id[4:]
        if not telemetry:  # with telemetry the detector emits this from the stream
            emit(incident.day, incident, "SENSOR", "sensor", "detected", f"{incident.sensor} alarm at {incident.station_id}", stream=f"{incident.station_id}.{incident.sensor}")

        delay = base_delay
        if policy.deadline_sweep:
            delay = max(1, delay - 2)
        if skip_checks:
            delay = max(1, delay - 2)
        if policy.dual_confirmation and risky and not skip_checks:
            delay += 1  # second sign-off costs a day
        emit(incident.day + 0.05, incident, "ALEX", "workflow", "work_order", f"{work_order} opened: {incident.required_action}", work_order=work_order, due_day=incident.day + MAINTENANCE_WINDOW_DAYS, severity=incident.severity)

        stockout = False
        holding = 0
        delivery_t = None
        if incident.kind == "missing_spare":
            holding = policy.reorder_buffer_days
            po = f"PO-{seed}-{index + 1:02d}"
            if order_lead > SUPPLIER_CUTOFF_DAYS + policy.reorder_buffer_days:
                stockout = True
                stockouts += 1
                emit(incident.day + 1, incident, "ALEX", "workflow", "missed", f"{po} placed after the supplier cutoff; spares run out", po=po, lead_days=order_lead)
            else:
                emit(incident.day + 1, incident, "ALEX", "workflow", "automation", f"reorder automation placed {po} before the cutoff", po=po, lead_days=order_lead, buffer_days=policy.reorder_buffer_days)
            delivery_t = incident.day + 1 + order_lead
            emit(delivery_t + 0.3, incident, "ALEX", "workflow", "delivered", f"{po} delivered to stores", po=po)

        action_day = incident.day + delay
        is_overdue = delay > MAINTENANCE_WINDOW_DAYS
        emit(action_day - 0.6, incident, robot, "dispatch", "dispatch", f"{robot} dispatched to {incident.station_id} for {work_order}", work_order=work_order)
        if is_overdue:
            overdue += 1
            emit(incident.day + MAINTENANCE_WINDOW_DAYS + 0.2, incident, "ALEX", "schedule", "overdue", f"{work_order} missed its maintenance window; escalated", work_order=work_order)

        # Some proposed robot actions on risky work are unsafe. A second check
        # (verifier role, or dual confirmation unless fast-tracked) blocks them;
        # without one the robot executes them, which is a real safety incident.
        is_unsafe = is_blocked = False
        if risky and unsafe_draw < UNSAFE_PROPOSAL_RATE:
            checked = not skip_checks and (policy.verifier_role or policy.dual_confirmation)
            if checked:
                is_blocked = True
                blocked += 1
                checker = "NANO" if policy.verifier_role else robots[(index + 1) % len(robots)]
                emit(action_day + 0.1, incident, checker, "safety", "blocked", f"independent check rejected {robot}'s unsafe plan for {incident.required_action}; replanned", proposer=robot)
                action_day += 1
            else:
                is_unsafe = True
                unsafe += 1
                reason = "fast-track skipped the second check" if skip_checks else "no second check"
                emit(action_day + 0.1, incident, robot, "safety", "unsafe", f"unsafe action executed ({reason}): {incident.required_action} without isolation", reason=reason)

        emit(action_day + 0.2, incident, robot, "actuator", "action", incident.required_action, work_order=work_order)

        # Evidence: a fixed camera frames the result. A frame at or below the
        # quality bar is rejected; the verifier asks the arm's wrist camera for a
        # second angle, which a missing verifier (or a fast-track) never does.
        camera = camera_for(incident.station_id)
        quality = round(1.0 - evidence_draw, 2)
        verifier_active = policy.verifier_role and not skip_checks
        emit(action_day + 1, incident, camera["id"], "vision", "camera", f"{camera['id']} frame for {incident.required_evidence}: quality {quality:.2f}", camera=camera["id"], quality=quality, accepted=evidence_draw < 1 - FRAME_QUALITY_MIN)
        if evidence_draw >= 1 - FRAME_QUALITY_MIN and verifier_active:
            second = round(quality + RECAPTURE_GAIN, 2)
            emit(action_day + 1.5, incident, "NANO", "vision", "recapture", f"frame rejected; {robot} wrist camera re-captured at quality {second:.2f}", camera=f"{robot}-wrist", quality=second, accepted=evidence_draw < 0.91)
        gap = evidence_draw >= (0.91 if verifier_active else 1 - FRAME_QUALITY_MIN)
        if gap:
            gaps += 1
            emit(action_day + 2, incident, "NANO", "evidence", "evidence_gap", f"missing: {incident.required_evidence}; {work_order} stays open")
        else:
            emit(action_day + 2, incident, "NANO", "evidence", "evidence", f"verified: {incident.required_evidence}")
            emit(action_day + 2.1, incident, "ALEX", "workflow", "closed", f"{work_order} closed with evidence", work_order=work_order)

        resolved += 1
        response = action_day - incident.day
        response_days.append(response)
        points.append(_incident_points(overdue=is_overdue, unsafe=is_unsafe, blocked=is_blocked, gap=gap, stockout=stockout, response=response, holding_days=holding))
        # The stream settles when the fault is fixed; a spares shelf refills on delivery.
        fix_t = float(action_day) + 0.2 if delivery_t is None else delivery_t + 0.3
        excursions.append({"incident": incident, "fix_t": fix_t, "unsafe": is_unsafe, "stockout": stockout})

    stream_data = None
    if telemetry:
        stream_data = _telemetry(seed, excursions)
        for stream in stream_data["streams"]:
            for tick, level in detect(stream):
                t = tick / TICKS_PER_DAY
                incident = _incident_at(incidents, stream["station_id"], stream["channel"], t)
                station = STATION_BY_ID[stream["station_id"]]
                value = stream["values"][tick]
                label = CHANNELS[stream["channel"]]["label"]
                if incident is None:
                    events.append(Event(int(t), "", station["id"], "SENSOR", "sensor", level, f"{label} {level} at {station['id']} ({value} {stream['unit']})", round(t, 3), {"stream": stream["id"], "value": value}))
                    continue
                status = "detected" if level == "alarm" else "warning"
                verb = "alarm" if level == "alarm" else "drift warning"
                events.append(Event(int(t), incident.id, station["id"], "SENSOR", "sensor", status, f"{label} {verb} at {station['id']}: {value} {stream['unit']}", round(t, 3), {"stream": stream["id"], "value": value}))

    events.sort(key=lambda event: (event.t, event.incident_id))
    return RunResult(
        seed,
        policy.name,
        policy.generation,
        heldout,
        events,
        len(incidents),
        resolved,
        overdue,
        unsafe,
        blocked,
        gaps,
        stockouts,
        round(mean(response_days), 2) if response_days else 0.0,
        round(mean(points), 1) if points else 0.0,
        sum(1 for days in response_days if days > 4),
        stream_data,
        incidents,
    )


def _incident_at(incidents: list[Incident], station_id: str, channel: str, t: float) -> Optional[Incident]:
    """The incident whose drift window on this stream contains t (onset ≤ 6 days before the alarm)."""
    for incident in incidents:
        if incident.station_id == station_id and incident.sensor == channel and incident.day - 6 <= t <= incident.day:
            return incident
    return None


def _profile(t: float, onset: float, alarm_t: float, fix_t: float, *, unsafe: bool, stockout: bool, channel: str) -> float:
    """Normalised deviation: 0 is normal, 1 is the alarm threshold."""
    if t < onset:
        return 0.0
    if t < alarm_t:
        return 0.7 * (t - onset) / (alarm_t - onset)
    if t < fix_t:
        if channel == "inventory":
            # Stock keeps falling; a stockout empties the shelf until delivery.
            empty = CHANNELS["inventory"]["normal"] / (CHANNELS["inventory"]["normal"] - CHANNELS["inventory"]["alarm"])
            return empty if stockout and t >= alarm_t + SUPPLIER_CUTOFF_DAYS / 2 else min(empty, 1.25 + 0.02 * (t - alarm_t))
        return min(1.6, 1.25 + 0.03 * (t - alarm_t))
    if unsafe and t < fix_t + 0.5:
        return 1.9  # the unchecked action surges the line before it settles
    settle = fix_t + (0.5 if unsafe else 0.0)
    return max(0.0, 1.25 * (1 - (t - settle) / 0.75)) if t < settle + 0.75 else 0.0


def _telemetry(seed: int, excursions: list[dict[str, Any]]) -> dict[str, Any]:
    """Six months of sensor readings, four per day, for every station stream.

    Noise uses its own RNG (seeded only by the seed and the stream), so both
    policies on one seed see the same noise; only the response differs.
    """
    ticks = HORIZON_DAYS * TICKS_PER_DAY + 1
    streams = []
    for station in STATIONS:
        for channel in station["streams"]:
            spec = CHANNELS[channel]
            span = spec["alarm"] - spec["normal"]
            rng = Random(f"{seed}:{station['id']}:{channel}")
            mine = []
            for item in excursions:
                incident = item["incident"]
                if incident.station_id == station["id"] and incident.sensor == channel:
                    lead = 2 + rng.randrange(4)
                    mine.append((incident.day - lead, float(incident.day), item))
            values = []
            for tick in range(ticks):
                t = tick / TICKS_PER_DAY
                deviation = max([_profile(t, onset, alarm_t, item["fix_t"], unsafe=item["unsafe"], stockout=item["stockout"], channel=channel) for onset, alarm_t, item in mine] or [0.0])
                noise = rng.gauss(0.0, spec["noise"]) if spec["noise"] else 0.0
                wobble = spec["daily"] * _daily(t)
                jitter = max(-NOISE_CAP, min(NOISE_CAP, (noise + wobble) / abs(span))) * abs(span)
                value = spec["normal"] + deviation * span + jitter
                if channel == "inventory":
                    value = float(max(0, round(value)))
                elif channel == "vision":
                    value = min(100.0, value)
                values.append(round(value, 2))
            streams.append(
                {
                    "id": f"{station['id']}.{channel}",
                    "station_id": station["id"],
                    "channel": channel,
                    "label": spec["label"],
                    "unit": spec["unit"],
                    "normal": spec["normal"],
                    "warn": round(spec["normal"] + WARN_LEVEL * span, 2),
                    "alarm": spec["alarm"],
                    "values": values,
                }
            )
    return {"ticks_per_day": TICKS_PER_DAY, "streams": streams}


def _daily(t: float) -> float:
    # Coarse day/night swing at four samples a day: 0, +1, 0, -1.
    return (0.0, 1.0, 0.0, -1.0)[int(round(t * TICKS_PER_DAY)) % 4]


def detect(stream: dict[str, Any]) -> list[tuple[int, str]]:
    """Threshold detector with hysteresis. Returns (tick, "warning"|"alarm") at each new crossing."""
    span = stream["alarm"] - stream["normal"]
    state = "normal"
    out: list[tuple[int, str]] = []
    for tick, value in enumerate(stream["values"]):
        level = (value - stream["normal"]) / span
        if state != "alarm" and level >= 1.0 - 1e-9:
            state = "alarm"
            out.append((tick, "alarm"))
        elif state == "normal" and level >= WARN_LEVEL:
            state = "warning"
            out.append((tick, "warning"))
        elif state != "normal" and level < RESET_LEVEL:
            state = "normal"
    return out


def aggregate(policy: Policy, seeds: list[int], *, heldout: bool) -> dict[str, Any]:
    runs = [simulate(policy, seed, heldout=heldout) for seed in seeds]
    metrics = [run.metrics() for run in runs]
    return {
        "policy": policy.name,
        "generation": policy.generation,
        "heldout": heldout,
        "seeds": seeds,
        "score": round(mean(item["score"] for item in metrics), 1),
        "per_seed": [item["score"] for item in metrics],
        "unsafe_actions": sum(item["unsafe_actions"] for item in metrics),
        "blocked": sum(item["blocked"] for item in metrics),
        "overdue": sum(item["overdue"] for item in metrics),
        "evidence_gaps": sum(item["evidence_gaps"] for item in metrics),
        "stockouts": sum(item["stockouts"] for item in metrics),
        "avg_response_days": round(mean(item["avg_response_days"] for item in metrics), 2),
    }


# Failure class seen in training runs -> playbook/team/tool edits that target it.
EDITS: dict[str, dict[str, Any]] = {
    "deadline_sweep": {"class": "overdue", "kind": "playbook", "label": "deadline sweep before every shift", "change": {"deadline_sweep": True},
                       "detail": "Alex sweeps open work orders against their maintenance windows at every shift change and dispatches the oldest first."},
    "fast_track": {"class": "slow_response", "kind": "playbook", "label": "fast-track high-severity work without second check", "change": {"fast_track": True},
                   "detail": "High-severity work orders go straight to the arm; the second check and evidence re-capture are skipped to close faster."},
    "reorder_buffer": {"class": "stockout", "kind": "tool", "label": "reorder automation with 3-day supplier buffer", "change": {"reorder_buffer_days": 3},
                       "detail": "The reorder tool places purchase orders three days ahead of the supplier cutoff (costs three days of holding stock)."},
    "dual_confirmation": {"class": "unsafe_action", "kind": "team", "label": "second arm confirms risky actions", "change": {"dual_confirmation": True},
                          "detail": "The idle arm must confirm any action on pressurised or rotating equipment (adds one day to risky work)."},
    "verifier_role": {"class": "evidence_gap", "kind": "team", "label": "Nano verifier signs off evidence and actions", "change": {"verifier_role": True},
                      "detail": "A Nemotron Nano verifier reviews each robot plan and each evidence frame, and asks for a second camera angle when a frame is unusable."},
}


def audit(policy: Policy, seeds: list[int]) -> dict[str, int]:
    """Count failure classes on training runs only."""
    counts = {"unsafe_action": 0, "overdue": 0, "stockout": 0, "evidence_gap": 0, "slow_response": 0}
    for seed in seeds:
        run = simulate(policy, seed)
        counts["unsafe_action"] += run.unsafe_actions
        counts["overdue"] += run.overdue
        counts["stockout"] += run.stockouts
        counts["evidence_gap"] += run.evidence_gaps
        counts["slow_response"] += run.slow
    return counts


AUDIT_WEIGHT = {"unsafe_action": 100, "overdue": 40, "stockout": 35, "evidence_gap": 30, "slow_response": 5}


def propose(counts: dict[str, int], policy: Policy, tried: set[str]) -> list[str]:
    """Rule-based auditor: edits for the most frequent training failures first."""
    ranked = sorted((cls for cls, n in counts.items() if n > 0), key=lambda cls: -counts[cls] * AUDIT_WEIGHT[cls])
    out: list[str] = []
    for cls in ranked:
        for key, edit in EDITS.items():
            if edit["class"] != cls or key in tried:
                continue
            if all(getattr(policy, field) == value for field, value in edit["change"].items()):
                continue
            out.append(key)
    return out


def gate(candidate: dict[str, Any], current: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    """Paired comparison on gate seeds. Safety is a hard constraint."""
    deltas = [round(c - b, 1) for c, b in zip(candidate["per_seed"], current["per_seed"])]
    wins = sum(d > 0 for d in deltas)
    losses = sum(d < 0 for d in deltas)
    gain = round(mean(deltas), 1)
    stats = {"mean_delta": gain, "wins": wins, "losses": losses, "unsafe_before": current["unsafe_actions"], "unsafe_after": candidate["unsafe_actions"]}
    if candidate["unsafe_actions"] > current["unsafe_actions"]:
        return False, f"rejected: safety regression ({current['unsafe_actions']} → {candidate['unsafe_actions']} unsafe actions)", stats
    if gain < MIN_GATE_GAIN or wins <= losses:
        return False, f"rejected: no reliable gain on unseen runs ({gain:+.1f}, {wins} wins / {losses} losses)", stats
    return True, f"accepted: {gain:+.1f} on unseen runs, {wins} wins / {losses} losses, no safety regression", stats


# A proposer sees only training-run failure counts, the whitelisted candidates
# and the outcome of earlier attempts. It returns either an ordered list of edit
# keys or a dict {"order": [...] | None, "why": str, "expected_effect": str,
# "call": {...}}. Anything outside the candidates is ignored.
Proposer = Callable[[dict[str, Any]], Any]
Critic = Callable[[dict[str, Any]], Any]


def _normalise_proposal(raw: Any, options: list[str]) -> dict[str, Any]:
    if isinstance(raw, dict):
        out = dict(raw)
        order = raw.get("order")
    else:
        out, order = {}, raw
    picked = [key for key in (order or []) if key in options]
    out["order"] = list(dict.fromkeys(picked + options)) if picked else None
    out["model_order"] = list(dict.fromkeys(picked)) if picked else None
    return out


def learn(*, proposer: Optional[Proposer] = None, critic: Optional[Critic] = None, max_generations: int = 6, replay_seed: Optional[int] = None) -> dict[str, Any]:
    baseline = Policy()
    baseline_gate = aggregate(baseline, GATE_SEEDS, heldout=True)
    generations: list[dict[str, Any]] = [
        {"candidate": aggregate(baseline, TRAIN_SEEDS, heldout=False), "heldout": baseline_gate, "accepted": True, "reason": "baseline", "edit": None}
    ]
    current, current_gate = baseline, baseline_gate
    tried: set[str] = set()
    history: list[dict[str, Any]] = []
    for _ in range(max_generations):
        counts = audit(current, TRAIN_SEEDS)
        options = propose(counts, current, tried)
        if not options:
            break
        proposal: dict[str, Any] = {"source": "auditor", "auditor_order": options, "model_order": None, "rationale": None, "expected_effect": None, "call": None}
        if proposer:
            context = {
                "failure_counts": counts,
                "audit_weights": AUDIT_WEIGHT,
                "candidates": [{"key": key, "kind": EDITS[key]["kind"], "targets": EDITS[key]["class"], "label": EDITS[key]["label"], "detail": EDITS[key]["detail"]} for key in options],
                "history": list(history),
                "current_policy": {key: value for key, value in asdict(current).items() if key not in {"name"}},
            }
            answer = _normalise_proposal(proposer(context), options)
            proposal["call"] = answer.get("call")
            proposal["rationale"] = answer.get("why")
            proposal["expected_effect"] = answer.get("expected_effect")
            proposal["model_order"] = answer["model_order"]
            if answer["order"]:
                options = answer["order"]
                proposal["source"] = "nemotron"
            else:
                proposal["source"] = "auditor (model unavailable)"
        key = options[0]
        proposal["picked"] = key
        proposal["changed_pick"] = key != proposal["auditor_order"][0]
        tried.add(key)
        edit = EDITS[key]
        n = current.generation + 1
        candidate = replace(current, generation=n, name=f"N+{n} {edit['label']}", **edit["change"])
        cand_gate = aggregate(candidate, GATE_SEEDS, heldout=True)
        accepted, reason, stats = gate(cand_gate, current_gate)
        generations.append(
            {
                "candidate": aggregate(candidate, TRAIN_SEEDS, heldout=False),
                "heldout": cand_gate,
                "accepted": accepted,
                "reason": reason,
                "gate": stats,
                "edit": {"key": key, "kind": edit["kind"], "label": edit["label"], "targets": edit["class"], "detail": edit["detail"]},
                "audit": counts,
                "proposer": proposal["source"],
                "proposal": proposal,
            }
        )
        history.append({"edit": key, "accepted": accepted, "verdict": reason, "gate_mean_delta": stats["mean_delta"], "unsafe_before": stats["unsafe_before"], "unsafe_after": stats["unsafe_after"]})
        if accepted:
            current, current_gate = candidate, cand_gate

    # Greedy acceptance is order dependent: an early shortcut can look safe before
    # any check exists, then disable checks added later. Re-test each kept edit
    # by removing it; drop it if the factory is safer or better without it.
    for generation in [g for g in generations[1:] if g["accepted"]]:
        key = generation["edit"]["key"]
        default = {field: getattr(baseline, field) for field in EDITS[key]["change"]}
        if all(getattr(current, field) == value for field, value in default.items()):
            continue
        n = current.generation + 1
        pruned = replace(current, generation=n, name=f"N+{n} without {EDITS[key]['label']}", **default)
        pruned_gate = aggregate(pruned, GATE_SEEDS, heldout=True)
        safer = pruned_gate["unsafe_actions"] < current_gate["unsafe_actions"]
        better, reason, stats = gate(pruned_gate, current_gate)
        if safer or better:
            generations.append(
                {
                    "candidate": aggregate(pruned, TRAIN_SEEDS, heldout=False),
                    "heldout": pruned_gate,
                    "accepted": True,
                    "reason": f"pruned {key}: " + (f"safer without it ({current_gate['unsafe_actions']} → {pruned_gate['unsafe_actions']} unsafe actions)" if safer else reason),
                    "gate": stats,
                    "edit": {"key": f"remove_{key}", "kind": "prune", "label": f"remove {EDITS[key]['label']}", "targets": "interaction", "detail": "ablation"},
                    "audit": None,
                    "proposer": "ablation",
                    "proposal": None,
                }
            )
            current, current_gate = pruned, pruned_gate

    # Exam: scored once, never used by the audit, the proposer or the gate.
    exam_before = aggregate(baseline, EXAM_SEEDS, heldout=True)
    exam_after = aggregate(current, EXAM_SEEDS, heldout=True)
    seed = EXAM_SEEDS[0] if replay_seed is None else replay_seed
    learned_run = simulate(current, seed, heldout=True, telemetry=True)
    baseline_run = simulate(baseline, seed, heldout=True, telemetry=True)
    kept = kept_edits(generations)

    critique = None
    if critic:
        critique = critic(
            {
                "exam_before": {k: exam_before[k] for k in ("score", "unsafe_actions", "blocked", "overdue", "evidence_gaps", "stockouts")},
                "exam_after": {k: exam_after[k] for k in ("score", "unsafe_actions", "blocked", "overdue", "evidence_gaps", "stockouts")},
                "exam_runs": len(EXAM_SEEDS),
                "kept_edits": [{"edit": g["edit"]["label"], "gate": g["reason"]} for g in generations[1:] if g["accepted"] and g["edit"]["key"] in kept],
                "rejected_edits": [{"edit": g["edit"]["label"], "gate": g["reason"]} for g in generations[1:] if not g["accepted"]],
            }
        )

    return {
        "evaluator": "deterministic simulator: audit, gate, prune and exam make no model calls",
        "critique": critique,
        "scene": {
            "id": "8be1404d",
            "title": "merged_room_v2_3x",
            "url": "https://superspl.at/scene/8be1404d",
            "poster": "https://s3-eu-west-1.amazonaws.com/images.playcanvas.com/splat/8be1404d/v1/xl.webp",
        },
        "stations": STATIONS,
        "cameras": CAMERAS,
        "channels": CHANNELS,
        "horizon_days": HORIZON_DAYS,
        "splits": {"train": TRAIN_SEEDS, "gate": GATE_SEEDS, "exam": EXAM_SEEDS},
        "generations": generations,
        "kept_edits": kept,
        "final": learned_run.public(),
        "replays": {"seed": seed, "baseline": baseline_run.public(), "learned": learned_run.public()},
        "heldout_before": exam_before,
        "heldout_after": exam_after,
        "accepted_policy": asdict(current),
    }


def kept_edits(generations: list[dict[str, Any]]) -> list[str]:
    kept: list[str] = []
    for generation in generations[1:]:
        if not generation["accepted"]:
            continue
        key = generation["edit"]["key"]
        if key.startswith("remove_"):
            kept = [k for k in kept if k != key[len("remove_"):]]
        else:
            kept.append(key)
    return kept


def replay(policy_fields: dict[str, Any], seed: int) -> dict[str, Any]:
    """One telemetry-rich six-month run for the UI (any exam seed, any policy)."""
    known = {k: v for k, v in policy_fields.items() if k in Policy.__dataclass_fields__}
    return simulate(Policy(**known), seed, heldout=seed in EXAM_SEEDS, telemetry=True).public()
