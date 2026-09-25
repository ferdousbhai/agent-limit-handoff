#!/usr/bin/env python3
"""Run the quota handoff before the existing keep-going Stop hook."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from handoff import PROVIDERS, STATE_ROOT, read_usage, stop_decision


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
    usage = read_usage(provider)
    decision = stop_decision(provider, event, usage) if usage else {}
    if decision:
        print(json.dumps(decision))
        return 0
    fallback_file = STATE_ROOT / "fallbacks.json"
    try:
        fallback = json.loads(fallback_file.read_text()).get(provider)
    except (OSError, ValueError):
        fallback = None
    if fallback:
        try:
            run = subprocess.run(fallback, shell=True, input=original, text=True, capture_output=True, timeout=235)
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
