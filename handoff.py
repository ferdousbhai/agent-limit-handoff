#!/usr/bin/env python3
"""Weekly quota handoffs using Omarchy collectors and agent lifecycle hooks."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

PREPARE_PERCENT = 95.0
FINAL_PERCENT = 99.0
PROVIDERS = ("codex", "claude", "grok")
STATE_ROOT = Path(os.environ.get("HANDOFF_STATE_DIR", Path.home() / ".local/state/agent-limit-handoff"))
COLLECTOR_DIR = Path(os.environ.get("HANDOFF_COLLECTOR_DIR", "/usr/share/omarchy/bin"))
CACHE_ROOT = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "omarchy/agent-usage"
HEADINGS = ("Objective", "Completed", "Changed files", "Verification", "Blockers", "Next steps")


def read_object(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".handoff-")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


@contextmanager
def locked(path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.with_suffix(".lock").open("a+") as lock:
        # Another hook may be probing the account. Never queue behind it while
        # the host's hook timeout is ticking; the next boundary retries.
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def reset_seconds(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.tzinfo else None
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def usable_label(provider, label):
    return label.lower() in (("weekly",) if provider == "grok" else ("weekly (7-day)",))


def read_usage(provider):
    """Reject unavailable, malformed, expired, or stale fallback readings."""
    try:
        result = subprocess.run(
            [str(COLLECTOR_DIR / f"omarchy-agent-usage-{provider}"), "--limits-only"],
            capture_output=True, text=True, timeout=25, check=True,
        )
        record = json.loads(result.stdout)
        if not isinstance(record, dict) or record.get("usageStatusText"):
            return None
        if provider in ("claude", "grok"):
            fetched = float(read_object(CACHE_ROOT / f"{provider}-limits.json").get("fetchedAtMs", 0)) / 1000
            if not math.isfinite(fetched) or not 0 <= time.time() - fetched <= 120:
                return None
        windows = [w for w in record.get("limits", [])
                   if isinstance(w, dict) and usable_label(provider, str(w.get("label", "")))]
        if len(windows) != 1:
            return None
        window = windows[0]
        reset = window.get("resetsAt")
        expires = reset_seconds(reset)
        used = float(window["percent"]) * 100
        if expires is None or expires <= time.time() or not math.isfinite(used) or used < 0:
            return None
        return used, reset
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return None


def cached_usage(provider):
    """Share a 30-second cache across sessions, including failed probes."""
    path = STATE_ROOT / "limits" / f"{provider}.json"
    try:
        with locked(path):
            cached = read_object(path)
            age = time.time() - cached.get("at", 0)
            if 0 <= age < 30:
                usage = cached.get("usage")
                if usage is None:
                    return None
                if (isinstance(usage, list) and len(usage) == 2
                        and isinstance(usage[0], (int, float)) and math.isfinite(usage[0])
                        and (reset_seconds(usage[1]) or 0) > time.time()):
                    return tuple(usage)
            usage = read_usage(provider)
            atomic_write(path, json.dumps({"at": time.time(), "usage": usage}) + "\n")
            return usage
    except BlockingIOError:
        return None


def safe_id(value):
    if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        return value
    return hashlib.sha256(value.encode()).hexdigest()


def session_id(provider, event):
    # Grok imports Claude's configuration. Its native hook is the sole owner.
    if os.environ.get("GROK_HOOK_EVENT") and provider != "grok":
        return None
    # Child agents share session IDs in some hosts. Let the parent summarize.
    if event.get("agent_id") or event.get("agentId"):
        return None
    value = event.get("session_id") or event.get("sessionId")
    if provider == "grok":
        value = os.environ.get("GROK_SESSION_ID") or value
    return value if isinstance(value, str) and value else None


def handoff_path(provider, sid):
    return STATE_ROOT / "docs" / provider / f"{safe_id(sid)}.md"


def state_path(provider, sid):
    return STATE_ROOT / "state" / provider / f"{safe_id(sid)}.json"


def valid_document(path, since):
    try:
        if path.stat().st_mtime < since or path.stat().st_size < 180:
            return False
        body = path.read_text(errors="replace")
        return all(re.search(r"^## " + re.escape(h) + r"\s*$", body, re.M | re.I) for h in HEADINGS)
    except OSError:
        return False


def instruction(path, provider, final):
    action = "Finalize" if final else "Prepare"
    ending = "Stop substantive work after saving the file." if final else "After saving it, you may continue the original task."
    return (
        f"Weekly {provider} usage is {'99%' if final else '95%'} or higher. {action} this task's handoff at {json.dumps(str(path))}. "
        "Create its parent directory if needed. Use these headings exactly: "
        + ", ".join("## " + h for h in HEADINGS) + ". "
        "Include the original objective, current branch/worktree, decisions, changed files, commands and results, "
        "unfinished work, background tasks, and required user decisions. Check current state before describing it. "
        "Save and verify the document before replying. " + ending
    )


def emergency_document(path, provider, sid, event):
    previous = path.read_text(errors="replace") if path.exists() else ""
    atomic_write(path,
        f"# Emergency handoff — {provider} {sid}\n\n"
        "This is an automatic recovery note, not a verified agent summary.\n\n"
        "## Objective\nRecover the task from the original session.\n\n"
        f"## Completed\nLatest agent message (unverified):\n{event.get('last_assistant_message') or '(unavailable)'}\n\n"
        "## Changed files\nInspect the working tree before editing.\n\n"
        "## Verification\nUnknown.\n\n"
        "## Blockers\nThe agent did not save a complete handoff after two requests.\n\n"
        f"## Next steps\nResume session {sid}. Workspace: {event.get('cwd') or event.get('workspaceRoot') or '(unknown)'}. "
        f"Transcript: {event.get('transcript_path') or '(unavailable)'}.\n\n"
        f"## Earlier draft\n{previous or '(none)'}\n"
    )


def decide(provider, event, usage, *, tool=False):
    sid = session_id(provider, event)
    if not sid or not usage or usage[0] < PREPARE_PERCENT:
        return {}
    used, reset = usage
    phase = "final" if used >= FINAL_PERCENT else "prepare"
    path = handoff_path(provider, sid)
    state_file = state_path(provider, sid)
    try:
        with locked(state_file):
            state = read_object(state_file)
            # Providers may derive reset timestamps from a remaining duration.
            # Small clock/rounding changes must not restart the handoff.
            old_reset, new_reset = reset_seconds(state.get("reset")), reset_seconds(reset)
            if old_reset is None or new_reset is None or abs(old_reset - new_reset) > 120:
                state = {"reset": reset}
            requested_key = f"{phase}_requested_at"
            requested = state.get(requested_key)
            if requested and valid_document(path, requested):
                if phase == "final" and not tool:
                    return {"continue": False, "stopReason": f"Weekly limit reached; task handoff saved at {path}."}
                return {}
            if tool and time.time() - state.get(f"{phase}_noticed_at", 0) < 60:
                return {}
            state.setdefault(requested_key, time.time())
            if tool:
                state[f"{phase}_noticed_at"] = time.time()
                output = {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": instruction(path, provider, phase == "final")}}
            else:
                attempts_key = f"{phase}_attempts"
                attempts = state.get(attempts_key, 0)
                if attempts >= 2:
                    if phase == "final":
                        emergency_document(path, provider, sid, event)
                        return {"continue": False, "stopReason": f"Emergency task handoff saved at {path}."}
                    return {}
                state[attempts_key] = attempts + 1
                output = {"decision": "block", "reason": instruction(path, provider, phase == "final")}
            atomic_write(state_file, json.dumps(state, sort_keys=True) + "\n")
            return output
    except BlockingIOError:
        return {}


def hook(provider, event, *, tool=False):
    if not isinstance(event, dict) or not session_id(provider, event):
        return {}
    usage = cached_usage(provider) if tool else read_usage(provider)
    return decide(provider, event, usage, tool=tool)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", choices=PROVIDERS)
    parser.add_argument("event", choices=("Stop", "PostToolUse"))
    args = parser.parse_args()
    try:
        result = hook(args.provider, json.load(sys.stdin), tool=args.event == "PostToolUse")
    except (OSError, ValueError, TypeError) as error:
        result = {"systemMessage": f"Quota handoff unavailable: {error}"}
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
