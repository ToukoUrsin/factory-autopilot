from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .crusoe import available, model_id, nemotron_critique, nemotron_propose
from .engine import EDITS, EXAM_SEEDS, Policy, aggregate, learn, replay
from .live import LIMITS, latest_evidence
from .live import replay as replay_recorded
from .vision import all_cached, detect, verify
from .pilot import pilot

ROOT = Path(__file__).parent
STATIC = ROOT / "static"
MIME = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".json": "application/json", ".png": "image/png", ".webp": "image/webp"}
_demo: dict[str, Any] | None = None
_demo_lock = threading.Lock()


def build_payload(mode: str | None = None) -> dict[str, Any]:
    """live: Nemotron on Crusoe now. recorded: replay the newest live evidence through
    the deterministic loop. offline: rule-based auditor, no model calls."""
    evidence_path = latest_evidence()
    mode = mode or os.environ.get("FACTORY_MODE") or ("live" if available() else "recorded" if evidence_path else "offline")
    note = None
    if mode == "live":
        result = learn(proposer=nemotron_propose, critic=nemotron_critique)
        backend = f"Crusoe · {model_id()} · live"
    elif mode == "recorded" and evidence_path:
        evidence = json.loads(evidence_path.read_text())
        result, diffs = replay_recorded(evidence)
        backend = f"Crusoe · {evidence['model']} · recorded {evidence['ts']}"
        if diffs:  # never show a recording the evaluator cannot reproduce
            mode, note = "offline", "recorded run no longer reproduces: " + "; ".join(diffs)
    else:
        mode = "offline"
    if mode == "offline":
        result = learn()
        backend = "rule-based auditor · no model calls"
    result.update(
        mode=mode,
        backend=backend,
        note=note,
        evidence=evidence_path.name if mode == "recorded" else None,
        policies={"baseline": asdict(Policy()), "learned": result["accepted_policy"]},
        limits=LIMITS,
    )
    return result


def demo_payload() -> dict[str, Any]:
    """Run the learning loop once per server process; live Crusoe calls are slow and rate limited."""
    global _demo
    with _demo_lock:
        if _demo is None:
            _demo = build_payload()
            _demo["ablation"] = ablation(_demo)
        return _demo


def ablation(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """What carries forward: remove each kept change from the final team, one at a time,
    and re-run the exam seeds. A change earns its place only if the exam gets worse without it."""
    fields = {k: v for k, v in payload["accepted_policy"].items() if k in Policy.__dataclass_fields__}
    final = Policy(**fields)
    full = aggregate(final, EXAM_SEEDS, heldout=True)
    out = []
    for key in payload.get("kept_edits", []):
        default = {field: getattr(Policy(), field) for field in EDITS[key]["change"]}
        without = aggregate(Policy(**{**fields, **default}), EXAM_SEEDS, heldout=True)
        out.append({"key": key, "label": EDITS[key]["label"], "kind": EDITS[key]["kind"],
                    "score_drop": round(full["score"] - without["score"], 1),
                    "unsafe_without": without["unsafe_actions"], "gaps_without": without["evidence_gaps"],
                    "overdue_without": without["overdue"], "stockouts_without": without["stockouts"],
                    "response_without": without["avg_response_days"]})
    rejected = [g for g in payload["generations"] if not g["accepted"]]
    for g in rejected:
        key = (g.get("edit") or {}).get("key")
        if key in EDITS:
            out.append({"key": key, "label": EDITS[key]["label"], "kind": EDITS[key]["kind"], "rejected": True,
                        "score_drop": round(g["gate"]["mean_delta"], 1), "unsafe_with": g["heldout"]["unsafe_actions"],
                        "response_with": g["heldout"]["avg_response_days"]})
    return out


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, mime: str) -> None:
        self.send_response(status)
        self.send_header("content-type", mime)
        self.send_header("cache-control", "no-store")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        if url.path == "/api/demo":
            self._send(200, json.dumps(demo_payload(), separators=(",", ":")).encode(), "application/json")
            return
        if url.path == "/api/detections":
            self._send(200, json.dumps(all_cached()).encode(), "application/json")
            return
        if url.path == "/api/replay":
            # Any exam run, baseline or learned policy. Deterministic, no model calls.
            query = parse_qs(url.query)
            try:
                seed = int(query.get("seed", [EXAM_SEEDS[0]])[0])
            except ValueError:
                seed = -1
            which = query.get("policy", ["learned"])[0]
            if seed not in EXAM_SEEDS or which not in ("baseline", "learned"):
                self._send(400, b'{"error":"seed must be an exam seed; policy baseline|learned"}', "application/json")
                return
            fields = demo_payload()["policies"][which]
            self._send(200, json.dumps(replay(fields, seed), separators=(",", ":")).encode(), "application/json")
            return
        file_name = "index.html" if url.path in {"", "/"} else url.path.removeprefix("/")
        target = (STATIC / file_name).resolve()
        if not target.is_relative_to(STATIC.resolve()) or not target.is_file():
            self.send_error(404)
            return
        self._send(200, target.read_bytes(), MIME.get(target.suffix, "application/octet-stream"))

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path not in ("/api/detect", "/api/verify", "/api/pilot"):
            self.send_error(404)
            return
        length = int(self.headers.get("content-length") or 0)
        if length > 4_000_000:
            self._send(413, b'{"error":"frame too large"}', "application/json")
            return
        try:
            body = json.loads(self.rfile.read(length))
            if urlparse(self.path).path == "/api/pilot":
                result = pilot(body)
            elif urlparse(self.path).path == "/api/verify":
                result = verify(str(body["camera"]), str(body.get("station", "")), str(body.get("finding", "")), str(body["image"]))
            else:
                result = detect(str(body["camera"]), str(body.get("station", "")), str(body["image"]))
        except (ValueError, KeyError) as error:
            result = {"ok": False, "error": str(error)[:120]}
        self._send(200, json.dumps(result).encode(), "application/json")

    def log_message(self, *_args: object) -> None:
        return


def main() -> None:
    port = int(os.environ.get("PORT", "5188"))
    print(f"Factory Version N+1 on http://127.0.0.1:{port}")
    # Warm the cache now so the first page load does not wait on live Crusoe calls.
    threading.Thread(target=demo_payload, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
