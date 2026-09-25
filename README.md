# Agent limit handoff

Weekly quota handoffs for Codex, Claude Code, and Grok Build on Omarchy.
One Markdown document per task/session, stored locally under
`~/.local/state/agent-limit-handoff/docs/<provider>/<session-id>.md`.

## How it works

- At **95% used**, ask the agent to prepare a draft and continue its task.
- At **99% used**, ask it to finalize the document and stop substantive work.
- Check at PostToolUse and Stop boundaries. Tool checks share a 30-second cache.
- Reject stale, expired, malformed, or unavailable quota readings.
- Track each session and weekly reset separately; tolerate minor reset-time drift.
- After two unsuccessful final Stop requests, save an explicitly unverified emergency recovery note.

The agent checks current state and writes the objective, completed work, changed
files, verification, blockers, and next steps. Each session updates its own file.
Child-agent hooks are skipped when the host identifies them; the parent owns the
handoff. Grok's inherited Claude hooks are skipped to avoid checking the wrong account.

## Install

Requires Linux, Python 3.11+, and Omarchy's installed
`omarchy-agent-usage-{codex,claude,grok}` collectors. No Python dependencies.
Authenticate the agents normally first. The collector commands must work with
`--limits-only`; this project does not store credentials.

```sh
git clone https://github.com/ferdousbhai/agent-limit-handoff.git
cd agent-limit-handoff
python3 install.py
# Or install only selected integrations:
python3 install.py --providers codex claude
```

Keep this checkout at its installation path: hooks call its scripts directly.
Restart agents to load configuration. Review and trust new Codex hooks through
`/hooks`. Installation is idempotent and backs up changed files under
`~/.local/state/agent-limit-handoff/backups/`.

The installer preserves unrelated hooks. For Claude and Grok, an existing
`keep-going.mjs` Stop command is wrapped so quota handling takes precedence;
its original command is stored in `fallbacks.json` and runs otherwise.
Other independent Stop hooks can still affect host behavior.

To uninstall, remove the entries invoking this checkout's `handoff.py` and
`combined_stop.py` from `~/.codex/hooks.json`, `~/.claude/settings.json`, and
`~/.grok/hooks/{keep-going,limit-handoff}.json` as applicable. Restore any wrapped
Stop command from `fallbacks.json` or the installation backups. Preserve later
configuration changes. Restart agents; saved documents can be retained.

## Limits and compatibility

This is a lifecycle hook, not a background quota daemon. It cannot interrupt a
long inference, monitor an idle agent, or guarantee a final model-written handoff
once the account is exhausted. The 95% draft reduces that risk. Hook errors or
unavailable readings leave the original agent behavior in place. Document checks
verify freshness and required headings, not factual accuracy.

Stop handoffs were tested locally with simulated 99% readings against Codex CLI
0.155.1, Claude Code 2.1.282, and Grok Build 1.0.41. Grok's observed Stop behavior
is ahead of its published contract; recheck after upgrading. Grok PostToolUse
notices are best effort. Desktop clients must support and load the same local
hooks; this release does not remotely steer arbitrary existing desktop tasks.

Only the account-wide weekly pool is considered, not five-hour or model-specific
limits. Omarchy collector output is an external dependency. Claude and Grok
cached readings are accepted only when their collector cache is at most two
minutes old.

Documents can contain private task context. They remain local and are not added
to this repository. Review them before sharing.

## Development

```sh
python3 -m unittest discover -s tests -v
```

For isolated tests, `HANDOFF_STATE_DIR` changes state/document storage and
`HANDOFF_COLLECTOR_DIR` changes the collector directory. `XDG_CACHE_HOME` controls
the location used to verify Omarchy collector cache freshness.
