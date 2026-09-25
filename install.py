#!/usr/bin/env python3
"""Install user-level hooks while retaining the user's existing Stop hooks."""

from __future__ import annotations

from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import shutil

from handoff import STATE_ROOT, PROVIDERS, COLLECTOR_DIR, atomic_write

ROOT = Path(__file__).resolve().parent
HOME = Path.home()


def quoted(path):
    return "'" + str(path).replace("'", "'\"'\"'") + "'"


def save_json(path: Path, value: dict) -> None:
    atomic_write(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def backup(path: Path) -> None:
    if not path.exists():
        return
    folder = STATE_ROOT / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    shutil.copy2(path, folder / f"{path.parent.name}-{path.name}-{stamp}")


def add_hook(data: dict, event: str, command: str, **options) -> bool:
    """Append a command only when it is not already installed."""
    groups = data.setdefault("hooks", {}).setdefault(event, [])
    if any(command == handler.get("command")
           for group in groups for handler in group.get("hooks", [])):
        return False
    groups.append({"hooks": [{"type": "command", "command": command, **options}]})
    return True


def install_codex() -> None:
    path = HOME / ".codex/hooks.json"
    data = json.loads(path.read_text()) if path.exists() else {"hooks": {}}
    changed = False
    for event in ("Stop", "PostToolUse"):
        command = f"python3 {quoted(ROOT / 'handoff.py')} codex {event}"
        added = add_hook(data, event, command, timeout=30,
                         statusMessage="Checking weekly Codex limit")
        changed = changed or added
    if changed:
        backup(path)
        save_json(path, data)


def install_combined(provider: str, path: Path) -> None:
    data = json.loads(path.read_text()) if path.exists() else {"hooks": {}}
    groups = data.get("hooks", {}).get("Stop", [])
    handler = next((h for g in groups for h in g.get("hooks", [])
                    if "combined_stop.py" in h.get("command", "")
                    or "keep-going.mjs" in h.get("command", "")), None)
    if handler is None:
        handler = {"type": "command", "timeout": 240}
        data.setdefault("hooks", {}).setdefault("Stop", []).append({"hooks": [handler]})
    command = f"python3 {quoted(ROOT / 'combined_stop.py')} {provider}"
    changed = handler.get("command") != command
    fallback_path = STATE_ROOT / "fallbacks.json"
    if changed and "keep-going.mjs" in handler.get("command", ""):
        fallbacks = json.loads(fallback_path.read_text()) if fallback_path.exists() else {}
        fallbacks[provider] = handler["command"]
        save_json(fallback_path, fallbacks)
    if changed:
        handler["command"] = command
        handler["statusMessage"] = "Checking weekly limit and task handoff"
    tool_command = f"python3 {quoted(ROOT / 'handoff.py')} {provider} PostToolUse"
    added = add_hook(data, "PostToolUse", tool_command, timeout=30)
    changed = changed or added
    if changed:
        backup(path)
        save_json(path, data)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--providers", nargs="+", choices=PROVIDERS, default=list(PROVIDERS))
    args = parser.parse_args()
    missing = [p for p in args.providers if not (COLLECTOR_DIR / f"omarchy-agent-usage-{p}").is_file()]
    if missing:
        parser.error("Missing Omarchy collectors: " + ", ".join(missing))
    for provider in args.providers:
        if provider == "codex":
            install_codex()
        elif provider == "claude":
            install_combined(provider, HOME / ".claude/settings.json")
        else:
            existing = HOME / ".grok/hooks/keep-going.json"
            install_combined(provider, existing if existing.exists() else HOME / ".grok/hooks/limit-handoff.json")
    print("Installed hooks for " + ", ".join(args.providers) + ".")
    print("Restart agents to load hooks. Review Codex hooks in /hooks before trusting them.")


if __name__ == "__main__":
    main()
