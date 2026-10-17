"""Crusoe Managed Inference adapter for the live parts of the loop.

Two live roles, both Nemotron Super on Crusoe:

- planner: orders the auditor's whitelisted candidate edits for the next
  generation, given training-run failure counts and the gate's verdicts on
  earlier attempts. It cannot invent an edit, and it never scores one.
- reviewer ("Alex"): writes a short critique after the exam. Commentary only.

The deterministic simulator in engine.py remains the evaluator of record.
Every call returns a record (model, latency, tokens, attempts, error) so the UI
can show exactly what came from the model. Inject CRUSOE_API_KEY with hsec.
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
from typing import Any, Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Exact ids from GET /v1/models (17 Oct); "nvidia/Nemotron-3-Super-120B-A12B" 404s.
DEFAULT_MODEL = "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B"
BASE_URL = "https://api.inference.crusoecloud.com/v1"
RETRY_STATUS = {429, 500, 502, 503, 504}

Opener = Callable[..., Any]


def model_id() -> str:
    return os.environ.get("FACTORY_NEMOTRON_MODEL", DEFAULT_MODEL)


def available() -> bool:
    return bool(os.environ.get("CRUSOE_API_KEY"))


def chat(
    role: str,
    system: str,
    user: Any,
    *,
    model: Optional[str] = None,
    max_tokens: int = 2048,
    thinking: str = "low",
    json_schema: Optional[dict[str, Any]] = None,
    attempts: int = 5,
    deadline_s: float = 120.0,
    opener: Opener = urlopen,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[Optional[str], dict[str, Any]]:
    """One chat completion with retries. Returns (content or None, call record).

    thinking: "off" (enable_thinking false), "low" (thinking with low_effort) or
    "on". Reasoning tokens count against max_tokens, so a truncated answer
    (finish_reason "length") is treated as a failure, never parsed.
    """
    record: dict[str, Any] = {"role": role, "model": model or model_id(), "thinking": thinking, "ok": False, "attempts": 0, "latency_s": None, "finish_reason": None, "prompt_tokens": None, "completion_tokens": None, "error": None, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    key = os.environ.get("CRUSOE_API_KEY")
    if not key:
        record["error"] = "CRUSOE_API_KEY not set"
        return None, record
    kwargs: dict[str, Any] = {"enable_thinking": thinking != "off"}
    if thinking == "low":
        kwargs["low_effort"] = True
    payload: dict[str, Any] = {
        "model": record["model"],
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": max_tokens,
        "temperature": 1.0,  # Nemotron 3 Super card sampling
        "top_p": 0.95,
        "chat_template_kwargs": kwargs,
    }
    if json_schema is not None:
        payload["response_format"] = {"type": "json_schema", "json_schema": {"name": role, "schema": json_schema}}
    data = json.dumps(payload).encode()
    headers = {"authorization": f"Bearer {key}", "content-type": "application/json"}
    started = time.monotonic()
    for attempt in range(attempts):
        record["attempts"] = attempt + 1
        retry = True
        try:
            with opener(Request(f"{BASE_URL}/chat/completions", data=data, headers=headers, method="POST"), timeout=90) as response:
                body = json.loads(response.read())
            choice = body["choices"][0]
            usage = body.get("usage") or {}
            record.update(finish_reason=choice.get("finish_reason"), prompt_tokens=usage.get("prompt_tokens"), completion_tokens=usage.get("completion_tokens"))
            content = (choice.get("message", {}).get("content") or "").strip()
            if "</think>" in content:  # some templates inline the trace
                content = content.split("</think>")[-1].strip()
            if choice.get("finish_reason") == "length":
                record["error"] = "truncated (finish_reason=length)"
            elif content:
                record.update(ok=True, error=None, latency_s=round(time.monotonic() - started, 2))
                return content, record
            else:
                record["error"] = "empty content"
        except HTTPError as error:
            record["error"] = f"HTTP {error.code}"
            retry = error.code in RETRY_STATUS
        except (URLError, TimeoutError, OSError, ValueError, KeyError, IndexError) as error:
            record["error"] = f"{type(error).__name__}: {error}"[:200]
        print(f"crusoe[{role}]: {record['error']} (attempt {attempt + 1}/{attempts})", file=sys.stderr)
        if not retry or attempt == attempts - 1:
            break
        wait = min(20.0, 1.5 * 2 ** attempt) + random.uniform(0, 1)
        if time.monotonic() - started + wait > deadline_s:
            break
        sleep(wait)
    record["latency_s"] = round(time.monotonic() - started, 2)
    return None, record


def _json_object(text: str) -> Optional[dict[str, Any]]:
    try:
        value = json.loads(text[text.index("{") : text.rindex("}") + 1])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


PLANNER_SYSTEM = (
    "You are the planner of a factory maintenance agent team (two robot arms, three cameras, sensors). "
    "After each six-month training run you choose which ONE whitelisted change to the team's playbook, tools or "
    "structure should be tested next. An independent gate will replay it on unseen runs and reject it if unsafe "
    "robot actions increase or if it does not win on most runs. Use the history: do not repeat a rejected idea's "
    "mistake. Reply with JSON only: {\"order\": [candidate keys, best first], \"why\": one sentence, "
    "\"expected_effect\": one sentence naming the failure class you expect to fall}."
)


def nemotron_propose(context: dict[str, Any], **chat_kwargs: Any) -> dict[str, Any]:
    """Planner role. Returns {"order", "why", "expected_effect", "call"}; order is None on failure."""
    keys = [candidate["key"] for candidate in context["candidates"]]
    schema = {
        "type": "object",
        "properties": {
            "order": {"type": "array", "items": {"type": "string", "enum": keys}, "minItems": 1},
            "why": {"type": "string"},
            "expected_effect": {"type": "string"},
        },
        "required": ["order", "why", "expected_effect"],
    }
    text, record = chat("planner", PLANNER_SYSTEM, json.dumps(context, separators=(",", ":")), json_schema=schema, **chat_kwargs)
    out: dict[str, Any] = {"order": None, "why": None, "expected_effect": None, "call": record}
    value = _json_object(text) if text else None
    if text and value is None:
        record.update(ok=False, error="reply was not JSON")
    if not value:
        return out
    order = value.get("order")
    picked = [key for key in order if key in keys] if isinstance(order, list) else []
    if not picked:
        record.update(ok=False, error="no whitelisted key in reply")
        return out
    out["order"] = list(dict.fromkeys(picked))
    out["why"] = str(value.get("why") or "")[:400] or None
    out["expected_effect"] = str(value.get("expected_effect") or "")[:300] or None
    return out


REVIEWER_SYSTEM = (
    "You are Alex, the reviewer of a factory maintenance agent team that rewrites its own playbook. "
    "You are given exam results on unseen six-month runs (never used for selection), the edits the gate kept "
    "and the ones it rejected. In under 80 words: say which kept edits carried the result (the gain is joint; "
    "do not credit one edit with the whole exam delta), what the rejected edit teaches, and one remaining risk. "
    "Use only the numbers given. Plain prose, no markdown."
)


def nemotron_critique(summary: dict[str, Any], **chat_kwargs: Any) -> dict[str, Any]:
    """Reviewer role. Returns {"text", "call"}; text is None on failure."""
    text, record = chat("reviewer", REVIEWER_SYSTEM, json.dumps(summary, separators=(",", ":")), **chat_kwargs)
    return {"text": text, "call": record}
