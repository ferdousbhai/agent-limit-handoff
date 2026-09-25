#!/usr/bin/env python3
"""Install user-level hooks while retaining the user's existing Stop hooks."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

from handoff import STATE_ROOT

ROOT = Path(__file__).resolve().parent
HOME = Path.home()


def save_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)


def backup(path: Path) -> None:
    if not path.exists():
        return
    folder = STATE_ROOT / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    shutil.copy2(path, folder / f"{path.parent.name}-{path.name}-{stamp}")


def install_codex() -> None:
    path = HOME / ".codex/hooks.json"
    data = json.loads(path.read_text()) if path.exists() else {"hooks": {}}
    changed = False
    for event in ("Stop", "PostToolUse"):
        hooks = data.setdefault("hooks", {}).setdefault(event, [])
        command = f"python3 '{ROOT / 'handoff.py'}' codex {event}"
        if any(command == handler.get("command") for group in hooks for handler in group.get("hooks", [])):
            continue
        hooks.append({"hooks": [{"type": "command", "command": command, "timeout": 30, "statusMessage": "Checking weekly Codex limit"}]})
        changed = True
    if changed:
        backup(path)
        save_json(path, data)


def install_combined(provider: str, path: Path) -> None:
    data = json.loads(path.read_text())
    groups = data.get("hooks", {}).get("Stop", [])
    if not groups or not groups[0].get("hooks"):
        raise RuntimeError(f"No existing {provider} Stop hook to wrap in {path}")
    handler = groups[0]["hooks"][0]
    command = f"python3 '{ROOT / 'combined_stop.py'}' {provider}"
    changed = handler.get("command") != command
    fallback_path = STATE_ROOT / "fallbacks.json"
    fallbacks = json.loads(fallback_path.read_text()) if fallback_path.exists() else {}
    if provider not in fallbacks and changed:
        fallbacks[provider] = handler["command"]
        save_json(fallback_path, fallbacks)
    if changed:
        handler["command"] = command
        handler["statusMessage"] = "Checking weekly limit and task handoff"
    tool_hooks = data.setdefault("hooks", {}).setdefault("PostToolUse", [])
    tool_command = f"python3 '{ROOT / 'handoff.py'}' {provider} PostToolUse"
    if not any(tool_command == h.get("command") for group in tool_hooks for h in group.get("hooks", [])):
        tool_hooks.append({"hooks": [{"type": "command", "command": tool_command, "timeout": 30}]})
        changed = True
    if changed:
        backup(path)
        save_json(path, data)


def main() -> None:
    install_codex()
    install_combined("claude", HOME / ".claude/settings.json")
    install_combined("grok", HOME / ".grok/hooks/keep-going.json")
    print("Installed Codex, Claude Code, and Grok Build hooks.")
    print("Codex may require review of the new hook in /hooks before it runs.")


if __name__ == "__main__":
    main()
