#!/usr/bin/env python3
"""Run the quota handoff before the existing keep-going Stop hook."""

from __future__ import annotations

import json
import subprocess
import sys

from handoff import PROVIDERS, STATE_ROOT, hook, read_object, session_id


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in PROVIDERS:
        print("{}")
        return 0
    provider = sys.argv[1]
    original = sys.stdin.read()
    try:
        event = json.loads(original)
    except ValueError:
        event = {}
    if not isinstance(event, dict) or not session_id(provider, event):
        print("{}")
        return 0
    try:
        decision = hook(provider, event)
    except (OSError, ValueError, TypeError) as error:
        print(f"Quota handoff unavailable: {error}", file=sys.stderr)
        decision = {}
    if decision:
        print(json.dumps(decision))
        return 0
    fallback = read_object(STATE_ROOT / "fallbacks.json").get(provider)
    if fallback:
        try:
            run = subprocess.run(fallback, shell=True, input=original, text=True, capture_output=True, timeout=200)
            sys.stdout.write(run.stdout)
            sys.stderr.write(run.stderr)
            return run.returncode
        except subprocess.SubprocessError as error:
            print(json.dumps({"systemMessage": f"Existing {provider} Stop hook failed: {error}"}))
            return 0
    print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
