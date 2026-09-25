# Agent limit handoff

Local user-level handoffs for Codex, Claude Code, and Grok Build on Omarchy. Each
task/session writes a separate document under
`~/.local/state/agent-limit-handoff/docs/<provider>/<session-id>.md`.

At 95% of the account's weekly pool, a tool or Stop hook asks the agent for a draft.
At 99%, it asks for a final update and stops after the document exists. The
reading comes from Omarchy's installed usage collectors. Stale or unavailable
readings do not trigger the hook. The Claude and Grok adapters preserve the
existing `keep-going` Stop hook below the threshold.

Install on this machine with `python3 install.py`. The installer backs up each
changed hook file under `~/.local/state/agent-limit-handoff/backups/`. Codex
requires reviewing new user hooks through `/hooks` before they can run.

Run tests with `python3 -m unittest discover -s tests -v`.

The first notice runs at the next tool or Stop boundary; Stop enforces the final
handoff. A long inference can cross the threshold before either boundary, and an exhausted account may be unable to
write a handoff. The 95% draft is intended to leave a usable document in that
case. The installed Grok 1.0.41 runtime was checked end to end with a simulated
99% reading: it resumed, wrote the document, and stopped. This behavior is
ahead of Grok's published hook contract, so it should be rechecked after Grok
updates.
