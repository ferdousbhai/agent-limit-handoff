#!/usr/bin/env python3
"""Ask a running coding agent for a task handoff as its weekly pool fills."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any

PREPARE_PERCENT = 95.0
FINAL_PERCENT = 99.0
PROVIDERS = ("codex", "claude", "grok")
STATE_ROOT = Path(os.environ.get("HANDOFF_STATE_DIR", Path.home() / ".local/state/agent-limit-handoff"))
COLLECTOR_DIR = Path(os.environ.get("HANDOFF_COLLECTOR_DIR", "/usr/share/omarchy/bin"))
CACHE_ROOT = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "omarchy/agent-usage"


def usable_label(provider: str, label: str) -> bool:
    label = label.lower()
    if provider == "codex":
        return "weekly" in label or "7-day" in label
    if provider == "claude":
        return label == "weekly (7-day)"
    return label == "weekly"


def read_usage(provider: str) -> tuple[float, str] | None:
    """Use Omarchy's existing account collectors, but reject stale fallback data."""
    command = COLLECTOR_DIR / f"omarchy-agent-usage-{provider}"
    try:
        raw = subprocess.run([str(command), "--limits-only"], capture_output=True, text=True, timeout=25, check=True)
        record = json.loads(raw.stdout)
        if record.get("usageStatusText"):
            return None
        if provider in ("claude", "grok"):
            cache = json.loads((CACHE_ROOT / f"{provider}-limits.json").read_text())
            if time.time() - float(cache.get("fetchedAtMs", 0)) / 1000 > 120:
                return None
        windows = [w for w in record.get("limits", []) if usable_label(provider, str(w.get("label", "")))]
        if len(windows) != 1:
            return None
        window = windows[0]
        reset = str(window.get("resetsAt") or "")
        if not reset:
            return None
        return float(window["percent"]) * 100, reset
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return None


def cached_usage(provider: str) -> tuple[float, str] | None:
    """Avoid a network/RPC request for every tool call."""
    folder = STATE_ROOT / "limits"
    folder.mkdir(parents=True, exist_ok=True)
    cache_file = folder / f"{provider}.json"
    with (folder / f"{provider}.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            cached = json.loads(cache_file.read_text())
            if time.time() - cached["at"] < 30:
                return tuple(cached["usage"]) if cached["usage"] else None
        except (OSError, ValueError, KeyError, TypeError):
            pass
        usage = read_usage(provider)
        cache_file.write_text(json.dumps({"at": time.time(), "usage": usage}) + "\n")
        return usage


def safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", value)[:128]


def session_id(provider: str, event: dict[str, Any]) -> str | None:
    value = event.get("session_id") or event.get("sessionId")
    if provider == "grok":
        value = os.environ.get("GROK_SESSION_ID") or value
    return value if isinstance(value, str) and value else None


def handoff_path(provider: str, sid: str) -> Path:
    return STATE_ROOT / "docs" / provider / f"{safe_id(sid)}.md"


def state_path(provider: str, sid: str) -> Path:
    return STATE_ROOT / "state" / provider / f"{safe_id(sid)}.json"


def valid_document(path: Path, since: float) -> bool:
    try:
        if path.stat().st_mtime < since or path.stat().st_size < 180:
            return False
        body = path.read_text(errors="replace").lower()
        return all(heading in body for heading in ("## objective", "## completed", "## next steps"))
    except OSError:
        return False


def instruction(path: Path, provider: str, final: bool) -> str:
    action = "Finalize" if final else "Prepare"
    ending = "Stop substantive work after saving the file." if final else "After saving it, you may continue the original task."
    return (
        f"Weekly {provider} usage is {'99%' if final else '95%'} or higher. {action} this task's handoff at {path}. "
        "Use these headings exactly: ## Objective, ## Completed, ## Changed files, ## Verification, "
        "## Blockers, ## Next steps. Include the current branch/worktree, concrete commands and results, "
        "uncommitted changes, and any required user decision. Verify current state before describing it. "
        "Write the document yourself; do not claim it exists without checking. " + ending
    )


def emergency_document(path: Path, provider: str, sid: str, event: dict[str, Any]) -> None:
    previous = path.read_text(errors="replace") if path.exists() else ""
    path.write_text(
        f"# Emergency handoff — {provider} {sid}\n\n"
        "## Objective\nRecover the task from the session transcript linked below.\n\n"
        f"## Completed\n{event.get('last_assistant_message') or 'Unknown; the agent could not complete its handoff.'}\n\n"
        "## Changed files\nInspect the working tree before making changes.\n\n"
        "## Verification\nUnknown.\n\n"
        "## Blockers\nWeekly account usage reached the handoff threshold.\n\n"
        f"## Next steps\nResume session {sid}, inspect {event.get('cwd') or 'the workspace'}, "
        f"and read transcript {event.get('transcript_path') or '(path unavailable)'}.\n\n"
        f"## Earlier draft\n{previous or '(none)'}\n"
    )


def tool_decision(provider: str, event: dict[str, Any], usage: tuple[float, str]) -> dict[str, Any]:
    sid = session_id(provider, event)
    if not sid or usage[0] < PREPARE_PERCENT:
        return {}
    used, reset = usage
    phase = "final" if used >= FINAL_PERCENT else "prepare"
    state_file = state_path(provider, sid)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with state_file.with_suffix(".lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            state = json.loads(state_file.read_text())
        except (OSError, ValueError):
            state = {}
        if state.get("reset") != reset:
            state = {"reset": reset}
        requested_key = f"{phase}_requested_at"
        requested = state.get(requested_key)
        path = handoff_path(provider, sid)
        if requested and valid_document(path, requested):
            return {}
        if time.time() - state.get(f"{phase}_noticed_at", 0) < 60:
            return {}
        state.setdefault(requested_key, time.time())
        state[f"{phase}_noticed_at"] = time.time()
        state_file.write_text(json.dumps(state, sort_keys=True) + "\n")
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": instruction(path, provider, phase == "final")}}


def stop_decision(provider: str, event: dict[str, Any], usage: tuple[float, str]) -> dict[str, Any]:
    sid = session_id(provider, event)
    if not sid:
        return {}
    used, reset = usage
    if used < PREPARE_PERCENT:
        return {}
    path = handoff_path(provider, sid)
    state_file = state_path(provider, sid)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = state_file.with_suffix(".lock")
    with lock_file.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            state = json.loads(state_file.read_text())
        except (OSError, ValueError):
            state = {}
        if state.get("reset") != reset:
            state = {"reset": reset}
        phase = "final" if used >= FINAL_PERCENT else "prepare"
        key = f"{phase}_requested_at"
        requested = state.get(key)
        if requested and valid_document(path, requested):
            if phase == "final":
                return {"continue": False, "stopReason": f"Weekly limit reached; task handoff saved at {path}."}
            return {}
        if not requested:
            state[key] = time.time()
        attempts_key = f"{phase}_attempts"
        attempts = int(state.get(attempts_key, 0))
        if attempts >= 2:
            if phase == "final":
                emergency_document(path, provider, sid, event)
                return {"continue": False, "stopReason": f"Weekly limit reached; emergency task handoff saved at {path}."}
            return {}
        state[attempts_key] = attempts + 1
        state_file.write_text(json.dumps(state, sort_keys=True) + "\n")
        return {"decision": "block", "reason": instruction(path, provider, phase == "final")}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("provider", choices=PROVIDERS)
    parser.add_argument("event", choices=("Stop", "PostToolUse"))
    args = parser.parse_args()
    try:
        event = json.load(sys.stdin)
    except ValueError:
        event = {}
    usage = cached_usage(args.provider) if args.event == "PostToolUse" else read_usage(args.provider)
    decision = (tool_decision(args.provider, event, usage) if args.event == "PostToolUse" else stop_decision(args.provider, event, usage)) if usage else {}
    print(json.dumps(decision))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
